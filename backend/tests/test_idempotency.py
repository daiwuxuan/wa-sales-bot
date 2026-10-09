"""幂等下单。

客户会连点、WhatsApp 会重投 webhook、模型可能在同一轮里重试。
这三种情况都必须落到同一张订单上。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.core.database import session_scope
from app.models import Customer, CustomerTier, Order, ProductVariant
from app.services import orders as order_service
from app.services import pricing
from app.services import quotes as quote_service


def _setup(*, stock=10, tier_code="retail", discount=0):
    """建一个客户 + 一个变体 + 一份报价，返回 (customer_id, variant_id, quote_no)。"""
    with session_scope() as session:
        tier = CustomerTier(code=tier_code, name=tier_code, default_discount_pct=discount)
        session.add(tier)
        session.flush()

        from app.models import Inventory, Product

        product = Product(sku="IDEM-001", name="幂等测试商品", is_active=True)
        session.add(product)
        session.flush()

        variant = ProductVariant(
            product_id=product.id, variant_sku="IDEM-001-black-XL",
            color_code="black", color_label="黑色",
            size_code="XL", size_label="XL",
            list_price_cents=19900, floor_price_cents=15900, is_active=True,
        )
        session.add(variant)
        session.flush()
        session.add(Inventory(variant_id=variant.id, qty_on_hand=stock))

        customer = Customer(phone="8613800138000", name="幂等客户", tier_id=tier.id)
        session.add(customer)
        session.flush()

        offer = pricing.compute_offer(variant, tier, 1)
        quote = quote_service.create_quote(
            session,
            customer=customer,
            tier=tier,
            conversation_id=None,
            entries=[quote_service.QuoteEntry(variant, offer)],
        )
        return customer.id, variant.id, quote.quote_no


def _place(customer_id: int, variant_id: int, quote_no: str):
    with session_scope() as session:
        customer = session.get(Customer, customer_id)
        variant = session.get(ProductVariant, variant_id)
        quote = quote_service.load_quote(session, quote_no)
        offer_id = quote.items[0].offer_id
        order, dedup = order_service.create_order(
            session,
            customer=customer,
            conversation_id=None,
            quote=quote,
            items=[{"variant_id": variant.id, "qty": 1, "offer_id": offer_id}],
        )
        return order.order_no, dedup


def test_same_quote_twice_yields_one_order():
    """客户连点两次「确认下单」-> 只有一张订单。"""
    customer_id, variant_id, quote_no = _setup()

    first_no, first_dedup = _place(customer_id, variant_id, quote_no)
    second_no, second_dedup = _place(customer_id, variant_id, quote_no)

    assert first_dedup is False
    assert second_dedup is True, "第二次必须被识别为重复提交"
    assert first_no == second_no, "必须返回同一张订单号"

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1


def test_duplicate_submit_does_not_double_deduct_stock():
    """重复提交不能二次扣库存 —— 这是最容易漏掉的一环。"""
    from app.models import Inventory

    customer_id, variant_id, quote_no = _setup(stock=10)
    _place(customer_id, variant_id, quote_no)
    _place(customer_id, variant_id, quote_no)

    with session_scope() as session:
        qty = session.scalar(
            select(Inventory.qty_on_hand).where(Inventory.variant_id == variant_id)
        )
    assert qty == 9, f"只该扣 1 件，实际扣到剩 {qty}"


def test_concurrent_duplicate_submit():
    """同一个报价被并发提交 8 次 -> 仍然只有一张订单。"""
    customer_id, variant_id, quote_no = _setup(stock=10)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _place(customer_id, variant_id, quote_no), range(8)))

    order_nos = {r[0] for r in results}
    assert len(order_nos) == 1, f"并发下产生了多张订单：{order_nos}"

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1


def test_deduplicated_response_is_not_an_error():
    """幂等命中要返回 ok=True 且带 deduplicated 标记。

    回一句「下单失败」比回一句「订单已生成」糟糕得多 —— 后者才是事实，
    而且客户连点两次时前者会让他以为没成功。
    """
    customer_id, variant_id, quote_no = _setup()
    _place(customer_id, variant_id, quote_no)
    _, dedup = _place(customer_id, variant_id, quote_no)

    with session_scope() as session:
        order = session.scalar(select(Order))
        payload = order_service.order_to_tool_payload(order, dedup)

    assert payload["ok"] is True
    assert payload["data"]["deduplicated"] is True
    assert "order_no" in payload["data"]


def test_order_cannot_exceed_quote_total():
    """下单金额必须等于报价金额，不能由调用方影响。"""
    customer_id, variant_id, quote_no = _setup()

    with session_scope() as session:
        customer = session.get(Customer, customer_id)
        quote = quote_service.load_quote(session, quote_no)
        order, _ = order_service.create_order(
            session,
            customer=customer,
            conversation_id=None,
            quote=quote,
            items=[{"variant_id": variant_id, "qty": 1, "offer_id": quote.items[0].offer_id}],
        )
        assert order.total_cents == quote.total_cents
