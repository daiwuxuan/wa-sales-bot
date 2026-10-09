"""定价规则引擎。

重点验证「AI 不可能编造价格」的两条结构性保证：
  1. 最低价守护：折扣再大也不会破线
  2. 工具返回值里不含任何未授权字段（最低价、成本等）
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.database import session_scope
from app.models import ProductVariant
from app.services import pricing
from app.services.pricing import MAX_TOTAL_DISCOUNT_PCT


class _Tier:
    def __init__(self, pct: int):
        self.default_discount_pct = pct
        self.code = f"t{pct}"


def _variant(*, price=19900, floor=15900):
    with session_scope() as session:
        from app.models import Inventory, Product

        product = Product(sku="PRICE-001", name="定价测试商品", is_active=True)
        session.add(product)
        session.flush()
        variant = ProductVariant(
            product_id=product.id, variant_sku="PRICE-001-black-XL",
            color_code="black", color_label="黑色",
            size_code="XL", size_label="XL",
            list_price_cents=price, floor_price_cents=floor, is_active=True,
        )
        session.add(variant)
        session.flush()
        session.add(Inventory(variant_id=variant.id, qty_on_hand=50))
        return variant.id


def test_no_discount_returns_list_price():
    vid = _variant()
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(0), 1)
    assert offer.unit_price_cents == 19900
    assert offer.discount_pct == 0


def test_tier_discount_applies():
    vid = _variant()
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(5), 1)
    # 19900 * 0.95 = 18905
    assert offer.unit_price_cents == 18905
    assert offer.discount_pct == 5


def test_floor_price_is_never_breached():
    """★ 核心保证：给一个荒唐的折扣率，成交价也必须停在最低价上。"""
    vid = _variant(price=19900, floor=15900)
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(90), 1)

    assert offer.unit_price_cents == 15900, "最低价守护失效了"
    assert offer.floor_hit is True


def test_discount_is_capped():
    """等级折扣 + 数量阶梯叠加也不能突破封顶。"""
    vid = _variant(price=100000, floor=1000)
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(90), 100)

    max_discount_cents = 100000 * (100 - MAX_TOTAL_DISCOUNT_PCT) // 100
    assert offer.unit_price_cents >= max_discount_cents


def test_tool_payload_leaks_no_floor_price():
    """★ 工具返回值必须严格白名单 —— 最低价绝不能进去。

    模型看不到的数字，它就不可能说出口。这是「客户问底价」这类
    提示注入攻击的根本防线。
    """
    vid = _variant(price=19900, floor=15900)
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(5), 1)
        payload = offer.to_tool_payload()

    serialized = str(payload)
    assert "floor" not in serialized.lower(), "最低价泄漏进模型上下文了"
    assert 15900 not in payload.values(), "最低价数值直接出现在返回值里"
    assert set(payload) == {
        "offer_id", "variant_id", "qty", "unit_price_cents", "unit_price_display",
        "list_price_cents", "discount_pct", "line_total_cents", "line_total_display",
    }, f"返回值字段被意外扩充了：{set(payload)}"


def test_line_total_matches_unit_price_times_qty():
    vid = _variant(price=10000, floor=1000)
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(0), 7)
    assert offer.line_total_cents == offer.unit_price_cents * 7


def test_effective_discount_reflects_floor_not_nominal():
    """触发最低价守护时，显示的折扣必须是真实生效的那个。

    名义折扣 90% 但实际只降到最低价 —— 界面上显示 90% 就是撒谎，
    报表也会跟着错。
    """
    vid = _variant(price=10000, floor=9000)
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        offer = pricing.compute_offer(variant, _Tier(90), 1)

    assert offer.unit_price_cents == 9000
    assert offer.discount_pct == 10, "应该显示实际生效的 10%，而不是名义的 90%"


def test_quantity_breaks_give_a_higher_tier():
    vid = _variant(price=10000, floor=1000)
    with session_scope() as session:
        variant = session.get(ProductVariant, vid)
        one = pricing.compute_offers(variant, _Tier(0), 1, available_qty=50)

    assert len(one) == 2, "除了客户问的数量，还应给一档加购建议"
    assert one[1].qty == 3
    assert one[1].unit_price_cents < one[0].unit_price_cents
