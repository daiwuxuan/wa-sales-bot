"""灌演示数据。

可以反复执行：每次先清空业务表再重建，所以随时能回到一个干净的演示状态。
用法：
    .venv/Scripts/python.exe seed.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from app.core.database import Base, engine, session_scope
from app.models import (
    AttributeAlias,
    Customer,
    CustomerTier,
    Inventory,
    MessageTemplate,
    Product,
    ProductVariant,
)

# ── 等级折扣 ────────────────────────────────────────────────
TIERS = [
    ("retail", "零售客户", 0, 1),
    ("silver", "银卡会员", 3, 2),
    ("gold", "金卡会员", 5, 3),
    ("vip", "VIP 客户", 8, 4),
]

# ── 属性别名 ────────────────────────────────────────────────
# 客户永远说中文，DB 永远存英文码。这张表就是中间的桥。
COLOR_ALIASES = [
    ("black", "黑色", "黑色"), ("black", "黑", "黑色"), ("black", "black", "黑色"),
    ("black", "哑光黑", "黑色"),
    ("white", "白色", "白色"), ("white", "白", "白色"), ("white", "white", "白色"),
    ("red", "红色", "红色"), ("red", "红", "红色"), ("red", "酒红", "红色"),
    ("navy", "藏青", "藏青色"), ("navy", "藏青色", "藏青色"), ("navy", "深蓝", "藏青色"),
    ("beige", "米色", "米色"), ("beige", "卡其", "米色"), ("beige", "杏色", "米色"),
]

SIZE_ALIASES = [
    ("S", "s", "S"), ("S", "小号", "S"), ("S", "小", "S"),
    ("M", "m", "M"), ("M", "中号", "M"), ("M", "中", "M"),
    ("L", "l", "L"), ("L", "大号", "L"), ("L", "大", "L"),
    ("XL", "xl", "XL"), ("XL", "加大", "XL"), ("XL", "加大码", "XL"), ("XL", "xlarge", "XL"),
    ("XXL", "xxl", "XXL"), ("XXL", "加加大", "XXL"), ("XXL", "2xl", "XXL"),
]

# ── 商品 ────────────────────────────────────────────────────
# (sku, 名称, 类目, 基准价元, 最低价元, [(颜色码, 颜色名, 尺码, 库存), ...])
# 最低价刻意低于零售折扣价能触到的位置，用来验证底线守护确实会生效。
PRODUCTS = [
    ("TSHIRT-001", "新疆长绒棉圆领T恤", "上衣", 199, 159, [
        ("black", "黑色", "M", 12), ("black", "黑色", "L", 8), ("black", "黑色", "XL", 3),
        ("white", "白色", "M", 15), ("white", "白色", "L", 6), ("white", "白色", "XL", 0),
    ]),
    ("HOODIE-002", "加绒连帽卫衣", "上衣", 359, 289, [
        ("black", "黑色", "L", 5), ("black", "黑色", "XL", 7),
        ("navy", "藏青色", "M", 4), ("navy", "藏青色", "L", 9),
        ("beige", "米色", "XL", 2),
    ]),
    ("JEANS-003", "弹力直筒牛仔裤", "裤装", 429, 349, [
        ("navy", "藏青色", "M", 6), ("navy", "藏青色", "L", 4),
        ("black", "黑色", "L", 11), ("black", "黑色", "XL", 1),
    ]),
    ("JACKET-004", "轻薄防风夹克", "外套", 699, 559, [
        ("black", "黑色", "L", 3), ("black", "黑色", "XL", 2),
        ("red", "红色", "M", 1),
    ]),
    ("DRESS-005", "法式碎花连衣裙", "裙装", 559, 449, [
        ("red", "红色", "S", 7), ("red", "红色", "M", 5),
        ("beige", "米色", "S", 3), ("beige", "米色", "M", 4),
    ]),
    ("SHORTS-006", "速干运动短裤", "裤装", 149, 119, [
        ("black", "黑色", "M", 20), ("black", "黑色", "L", 18), ("black", "黑色", "XL", 14),
    ]),
]

CUSTOMERS = [
    ("8613800138000", "张伟", "vip"),
    ("8613900139000", "李娜", "gold"),
    ("8613700137000", "王芳", "silver"),
    ("8613600136000", "刘强", "retail"),
]

TEMPLATES = [
    ("season_sale", "seasonal", "换季清仓，全场 {{1}} 折起，回复「想要」看新款。"),
    ("vip_thanks", "marketing", "{{1}} 您好，感谢支持，专属 {{2}} 已到账。"),
]


def clear_all() -> None:
    """先删干净再加，保证 seed 可重复执行。

    删除顺序由 services.maintenance 统一负责 —— orders 和 quotes
    之间有循环外键，顺序写错就会撞 FK。
    """
    from app.services.maintenance import wipe_business_data

    with session_scope() as db:
        wipe_business_data(db)


def seed() -> None:
    Base.metadata.create_all(engine)
    clear_all()

    now = datetime.now(UTC)

    with session_scope() as db:
        tiers: dict[str, CustomerTier] = {}
        for code, name, pct, order in TIERS:
            tier = CustomerTier(code=code, name=name, default_discount_pct=pct, sort_order=order)
            db.add(tier)
            tiers[code] = tier
        db.flush()

        for canonical, alias, label in COLOR_ALIASES:
            db.add(AttributeAlias(attr_type="color", alias=alias, canonical=canonical, label=label))
        for canonical, alias, label in SIZE_ALIASES:
            db.add(AttributeAlias(attr_type="size", alias=alias, canonical=canonical, label=label))

        variant_count = 0
        for sku, name, category, price, floor, variants in PRODUCTS:
            product = Product(
                sku=sku, name=name, category=category,
                description=f"{name}，{category}类目热销款。",
                image_url=None,  # 图片由商家在后台自己上传
                is_active=True,
            )
            db.add(product)
            db.flush()

            for color_code, color_label, size_code, qty in variants:
                list_cents = price * 100
                # 尺码加价：XL/XXL 各加 10 元，让变体价格有差异
                if size_code in ("XL", "XXL"):
                    list_cents += 1000
                variant = ProductVariant(
                    product_id=product.id,
                    variant_sku=f"{sku}-{color_code[:3].upper()}-{size_code}",
                    color_code=color_code, color_label=color_label,
                    size_code=size_code, size_label=size_code,
                    list_price_cents=list_cents,
                    floor_price_cents=floor * 100,
                    weight_grams=300,
                    is_active=True,
                )
                db.add(variant)
                db.flush()
                db.add(Inventory(
                    variant_id=variant.id,
                    qty_on_hand=qty, qty_reserved=0, low_stock_threshold=5,
                ))
                variant_count += 1

        for phone, cname, tier_code in CUSTOMERS:
            db.add(Customer(
                phone=phone, name=cname, tier_id=tiers[tier_code].id,
                first_seen_at=now, last_seen_at=now,
            ))

        for tname, category, body in TEMPLATES:
            db.add(MessageTemplate(
                name=tname, category=category, body_template=body,
                wa_template_status="none", is_active=True,
            ))

    product_count = len(PRODUCTS)

    # 给一个客户预置一条会话，方便直接演示
    from app.services.customers import get_or_create_conversation, get_customer_by_phone

    with session_scope() as db:
        customer = get_customer_by_phone(db, CUSTOMERS[3][0])
        if customer:
            get_or_create_conversation(db, customer)

    print(f"seed 完成：{product_count} 个商品 / {variant_count} 个变体 / {len(CUSTOMERS)} 个客户")
    print("未上传商品图 —— 到后台「商品管理」页面上传后，AI 才能给客户发图。")


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:  # noqa: BLE001
        print(f"seed 失败: {exc}", file=sys.stderr)
        raise
