"""商品与库存查询、属性归一化。

★ 属性归一化是主链路能不能跑通的关键。
客户说「有黑色 XL 吗」，模型会传 color="黑色" 或 "黑"，
而 DB 里存的是 black。没有这一层，search_inventory 恒返回空，
客户第一句话就得不到答案。
"""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import AttributeAlias, Inventory, Product, ProductVariant


def _norm(text: str) -> str:
    """归一化用户输入：去空白、转小写、去掉常见修饰。"""
    return re.sub(r"\s+", "", (text or "").strip().lower())


def resolve_attribute(db: Session, attr_type: str, raw: str | None) -> str | None:
    """把用户/模型给的属性值翻译成规范码。

    已经是规范码的直接放行；否则查别名表。
    查不到返回 None —— 由调用方决定是报错还是忽略该维度。
    """
    if not raw:
        return None
    key = _norm(raw)

    hit = db.scalar(
        select(AttributeAlias).where(
            AttributeAlias.attr_type == attr_type,
            or_(
                func.lower(AttributeAlias.alias) == key,
                func.lower(AttributeAlias.canonical) == key,
            ),
        )
    )
    return hit.canonical if hit else None


def load_alias_map(db: Session, attr_type: str) -> dict[str, str]:
    """取某属性的全部别名 -> 规范码映射。Mock LLM 抽取用。"""
    rows = db.scalars(select(AttributeAlias).where(AttributeAlias.attr_type == attr_type))
    out: dict[str, str] = {}
    for r in rows:
        out[_norm(r.alias)] = r.canonical
        out[_norm(r.canonical)] = r.canonical
    return out


def extract_attributes(db: Session, text: str) -> dict[str, str | None]:
    """从自由文本里扫出颜色和尺码。

    只给 Mock LLM 用 —— 真实 GPT-4o 由模型自己理解语义并调用工具。
    对 Mock 来说这一步是必需的：它得从「有黑色XL吗」里抠出 black / XL。
    """
    norm = _norm(text)
    found: dict[str, str | None] = {"color": None, "size": None}

    for attr_type in ("color", "size"):
        aliases = load_alias_map(db, attr_type)
        # 长别名优先：「加大码」要先于「加大」匹配，否则会被短别名截胡
        for alias in sorted(aliases, key=len, reverse=True):
            if alias and alias in norm:
                found[attr_type] = aliases[alias]
                break
    return found


def search_variants(
    db: Session,
    *,
    sku: str | None = None,
    color: str | None = None,
    size: str | None = None,
    limit: int = 10,
) -> list[ProductVariant]:
    """按款号/颜色/尺码查变体。三个条件都可以为空，但至少要给一个。

    颜色和尺码在这里再归一化一次 —— 不能只依赖模型传规范码。
    模型看到 schema 里的 enum 也可能传中文，跳过这一步就查空了。
    """
    stmt = (
        select(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .options(selectinload(ProductVariant.product), selectinload(ProductVariant.inventory))
        .where(ProductVariant.is_active.is_(True), Product.is_active.is_(True))
    )

    if sku:
        # 允许传款号（TSHIRT-001）或变体号（TSHIRT-001-BLK-XL）
        sku_norm = _norm(sku)
        stmt = stmt.where(
            or_(
                func.lower(Product.sku) == sku_norm,
                func.lower(ProductVariant.variant_sku) == sku_norm,
            )
        )

    if color:
        canonical = resolve_attribute(db, "color", color)
        if canonical is None:
            return []  # 颜色不在词表里 -> 明确查不到，而不是退化成「忽略该条件」
        stmt = stmt.where(ProductVariant.color_code == canonical)

    if size:
        canonical = resolve_attribute(db, "size", size)
        if canonical is None:
            return []
        stmt = stmt.where(ProductVariant.size_code == canonical)

    return list(db.scalars(stmt.limit(limit)))


def available_options(db: Session, product_id: int | None = None) -> dict:
    """某个款（或全库）有哪些颜色尺码可选。

    ★ 这是「查不到货」时的救命信息。把它回给模型，模型就能说
      「黑色 XL 没有，但有黑色 L 和白色 XL」，而不是干巴巴地回一句
      「没找到」然后对话死掉。
    """
    stmt = select(ProductVariant.color_code, ProductVariant.size_code).where(
        ProductVariant.is_active.is_(True)
    )
    if product_id is not None:
        stmt = stmt.where(ProductVariant.product_id == product_id)
    rows = db.execute(stmt).all()
    return {
        "colors": sorted({r[0] for r in rows}),
        "sizes": sorted({r[1] for r in rows}),
    }


def get_variant(db: Session, variant_id: int) -> ProductVariant | None:
    return db.scalar(
        select(ProductVariant)
        .options(selectinload(ProductVariant.product), selectinload(ProductVariant.inventory))
        .where(ProductVariant.id == variant_id)
    )


def get_inventory(db: Session, variant_id: int) -> Inventory | None:
    return db.scalar(select(Inventory).where(Inventory.variant_id == variant_id))


def find_primary_image_key(variant: ProductVariant) -> str | None:
    """商品图查找顺序：变体图（暂无）-> 商品主图。

    图片是商家在后台自己上传的，这里就是一次纯 DB 查询，
    没有任何图像生成调用。
    """
    product = variant.product
    return product.image_url if product else None
