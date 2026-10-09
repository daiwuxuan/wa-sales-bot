"""商品管理：列表、改价改库存、图片上传。

商品图由商家自己上传，AI 只负责读 URL 发给客户 —— 没有任何图像生成。
"""

from __future__ import annotations

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.ids import new_storage_key
from app.core.money import to_cents, to_yuan
from app.models import Inventory, Product, ProductVariant

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/products", tags=["products"])

ALLOWED_EXTENSIONS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}
# 显式拒绝 SVG：它能内嵌脚本，是一个 XSS 载体，而且这里完全用不到矢量图
FORBIDDEN_EXTENSIONS = {".svg", ".svgz", ".html", ".htm", ".js"}

# Pillow 认出的格式名 -> 我们允许的扩展名。
# 注意不能再用标准库的 imghdr —— 它在 Python 3.13 里已经被移除了。
PILLOW_FORMAT_TO_EXT = {
    "JPEG": {".jpg", ".jpeg"},
    "PNG": {".png"},
    "WEBP": {".webp"},
    "GIF": {".gif"},
}


def _sniff_image(content: bytes) -> str | None:
    """用图片解码器判断真实格式，而不是看文件名或 Content-Type。

    这是唯一可靠的做法：Content-Type 和扩展名都是客户端说了算的。
    Pillow 还会顺带拒绝掉那些「改了扩展名的假图片」。
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as img:
            img.verify()  # 真正解码一遍，能挡掉构造出来的畸形文件
            return img.format
    except Exception:  # noqa: BLE001
        return None


def _serialize_variant(variant: ProductVariant) -> dict:
    inv = variant.inventory
    return {
        "id": variant.id,
        "variant_sku": variant.variant_sku,
        "color_code": variant.color_code,
        "color_label": variant.color_label,
        "size_code": variant.size_code,
        "size_label": variant.size_label,
        "list_price_cents": variant.list_price_cents,
        "list_price_display": f"¥{to_yuan(variant.list_price_cents):.2f}",
        "floor_price_cents": variant.floor_price_cents,
        "floor_price_display": f"¥{to_yuan(variant.floor_price_cents):.2f}",
        "qty_on_hand": inv.qty_on_hand if inv else 0,
        "qty_reserved": inv.qty_reserved if inv else 0,
        "available_qty": inv.available_qty if inv else 0,
        "is_active": variant.is_active,
    }


def _serialize_product(product: Product) -> dict:
    total_stock = sum((v.inventory.available_qty if v.inventory else 0) for v in product.variants)
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "category": product.category,
        "image_url": product.image_url,
        "image_url_absolute": settings.public_url_for(product.image_url),
        "is_active": product.is_active,
        "variant_count": len(product.variants),
        "total_stock": total_stock,
        "variants": [_serialize_variant(v) for v in product.variants],
    }


@router.get("")
def list_products(
    db: Session = Depends(get_db),
    include_inactive: bool = False,
) -> dict:
    stmt = (
        select(Product)
        .options(
            selectinload(Product.variants).selectinload(ProductVariant.inventory)
        )
        .order_by(Product.id)
    )
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))

    return {"items": [_serialize_product(p) for p in db.scalars(stmt)]}


@router.get("/{sku}")
def get_product(sku: str, db: Session = Depends(get_db)) -> dict:
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.variants).selectinload(ProductVariant.inventory))
        .where(Product.sku == sku)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return _serialize_product(product)


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    is_active: bool | None = None


@router.patch("/{sku}")
def update_product(sku: str, body: ProductUpdate, db: Session = Depends(get_db)) -> dict:
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.variants).selectinload(ProductVariant.inventory))
        .where(Product.sku == sku)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return _serialize_product(product)


class VariantUpdate(BaseModel):
    list_price: float | None = Field(default=None, description="售价（元）")
    floor_price: float | None = Field(default=None, description="最低价（元）")
    qty_on_hand: int | None = Field(default=None, ge=0)
    low_stock_threshold: int | None = None
    is_active: bool | None = None


@router.patch("/variants/{variant_id}")
def update_variant(
    variant_id: int, body: VariantUpdate, db: Session = Depends(get_db)
) -> dict:
    variant = db.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="变体不存在")

    if body.list_price is not None:
        variant.list_price_cents = to_cents(body.list_price)
    if body.floor_price is not None:
        floor = to_cents(body.floor_price)
        # 最低价高于售价 = 永远卖不出去，且下单时 CHECK 约束会直接拒单。
        # 在入口挡住，比让客户下单时才炸要好。
        if floor > variant.list_price_cents:
            raise HTTPException(status_code=400, detail="最低价不能高于售价")
        variant.floor_price_cents = floor
    if body.is_active is not None:
        variant.is_active = body.is_active

    inv = db.scalar(select(Inventory).where(Inventory.variant_id == variant_id))
    if inv is None:
        inv = Inventory(variant_id=variant_id, qty_on_hand=0, qty_reserved=0)
        db.add(inv)
    if body.qty_on_hand is not None:
        if body.qty_on_hand < inv.qty_reserved:
            raise HTTPException(
                status_code=400,
                detail=f"库存不能低于已预占的 {inv.qty_reserved} 件",
            )
        inv.qty_on_hand = body.qty_on_hand
    if body.low_stock_threshold is not None:
        inv.low_stock_threshold = body.low_stock_threshold

    db.commit()
    db.refresh(variant)
    return _serialize_variant(variant)


@router.post("/{sku}/image")
def upload_product_image(
    sku: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """上传商品图。存本地磁盘，库里只记根相对路径。

    只存相对路径（/uploads/xxx.jpg）而不是完整 URL ——
    将来换域名或上 CDN，要改的是配置，不是每一行数据。
    """
    import pathlib

    product = db.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    filename = file.filename or ""
    ext = pathlib.Path(filename).suffix.lower()

    if ext in FORBIDDEN_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{ext}")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"只支持 {'/'.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content = file.file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {settings.max_upload_bytes // 1024 // 1024}MB 限制",
        )

    # ★ 不信任 Content-Type 和文件名，用解码器判断真实类型。
    #   然后把文件名整个换掉 —— 直接用客户端文件名是路径穿越漏洞
    #   （../../../.env 这种）。
    detected = _sniff_image(content)
    if detected is None:
        raise HTTPException(status_code=400, detail="文件内容不是有效图片")

    allowed_exts = PILLOW_FORMAT_TO_EXT.get(detected)
    if allowed_exts is None:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式：{detected}")
    if ext not in allowed_exts:
        # 扩展名和真实内容对不上。以真实内容为准重写扩展名。
        ext = sorted(allowed_exts)[0]

    key = new_storage_key(filename)
    target = settings.upload_dir / f"{key}{ext}"
    target.write_bytes(content)

    # 换图时删掉旧文件，避免磁盘越用越多
    if product.image_url:
        old = settings.upload_dir / pathlib.Path(product.image_url).name
        if old.exists() and old != target:
            try:
                old.unlink()
            except OSError:
                logger.warning("旧图删除失败: %s", old)

    product.image_url = f"/uploads/{target.name}"
    db.commit()
    db.refresh(product)

    return {
        "ok": True,
        "image_url": product.image_url,
        "image_url_absolute": settings.public_url_for(product.image_url),
        "size_bytes": len(content),
    }


@router.delete("/{sku}/image")
def delete_product_image(sku: str, db: Session = Depends(get_db)) -> dict:
    import pathlib

    product = db.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    if product.image_url:
        old = settings.upload_dir / pathlib.Path(product.image_url).name
        if old.exists():
            try:
                old.unlink()
            except OSError:
                pass
    product.image_url = None
    db.commit()
    return {"ok": True, "image_url": None}
