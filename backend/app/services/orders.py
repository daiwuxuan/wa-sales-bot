"""★ 幂等下单。

幂等键由服务端生成，模型无权提供 —— 否则模型可以靠编造 key 绕过幂等，
或者靠重复 key 把两笔真实订单合并成一笔。

首选键 quote:{quote_no}：
  报价天然与「这一次购买意图」一一对应，且消费后会置 consumed。
  客户过一会儿真想再买一件 -> 会走一次新的 get_quote -> 新 quote_no -> 新订单。
  不会被时间窗口误吞（纯时间桶键有这个坑：客户 5 分钟后复购会被当成重复提交）。

兜底键 cart:{hash}：
  给后台人工建单、或没有报价的路径用。
"""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import InvalidArgument, PriceChangedDuringCheckout
from app.core.ids import next_order_no
from app.models import Customer, Order, OrderItem, Quote
from app.services import inventory as inventory_service
from app.services import quotes as quote_service


def build_idempotency_key(
    phone: str, quote: Quote | None, items: list[dict]
) -> str:
    if quote is not None:
        return f"quote:{quote.quote_no}"

    fingerprint = ",".join(
        f"{i['variant_id']}:{i['qty']}" for i in sorted(items, key=lambda x: x["variant_id"])
    )
    bucket = int(time.time() // 300)  # 5 分钟窗口
    digest = hashlib.sha256(f"{phone}|{fingerprint}|{bucket}".encode()).hexdigest()
    return f"cart:{digest[:32]}"


def _load_existing(db: Session, key: str) -> Order | None:
    return db.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.idempotency_key == key)
    )


def create_order(
    db: Session,
    *,
    customer: Customer,
    conversation_id: int | None,
    quote: Quote | None,
    items: list[dict],
) -> tuple[Order, bool]:
    """建单。返回 (订单, 是否命中幂等)。

    ★ 幂等命中对客户永远不是错误。
      WhatsApp 会重投消息，客户也会连点。这种情况下回一句「下单失败」
      比回一句「您的订单已生成」糟糕得多 —— 后者才是事实。
    """
    if not items:
        raise InvalidArgument("订单至少要有一个商品")

    key = build_idempotency_key(customer.phone, quote, items)

    # 快速路径：重复请求直接返回原单，不碰库存也不碰报价
    if (existing := _load_existing(db, key)) is not None:
        return existing, True

    if quote is not None:
        quote_service.validate_for_checkout(
            db, quote, customer=customer, conversation_id=conversation_id
        )

    # 按 offer_id 从报价里取回服务端算好的单价。
    # 调用方传什么价格都没用 —— 参数里根本没有价格字段。
    priced_lines: list[dict] = []
    for requested in items:
        variant_id = int(requested["variant_id"])
        qty = int(requested["qty"])
        if qty < 1:
            raise InvalidArgument("数量必须大于 0", variant_id=variant_id)

        if quote is None:
            raise InvalidArgument("下单必须基于一份有效报价", variant_id=variant_id)

        offer_id = requested.get("offer_id")
        quote_item = next(
            (qi for qi in quote.items if qi.offer_id == offer_id and qi.variant_id == variant_id),
            None,
        )
        if quote_item is None:
            raise InvalidArgument(
                "报价里没有这个选项，请重新报价",
                variant_id=variant_id,
                offer_id=offer_id,
            )
        if qty != quote_item.qty:
            raise InvalidArgument(
                "数量与报价不一致，请重新报价",
                variant_id=variant_id,
                quoted_qty=quote_item.qty,
                requested_qty=qty,
            )

        priced_lines.append(
            {
                "variant_id": variant_id,
                "qty": qty,
                "unit_price_cents": quote_item.unit_price_cents,
                "list_price_cents": quote_item.list_price_cents,
                "floor_price_cents": quote_item.floor_price_cents,
                "discount_pct": quote_item.discount_pct,
                "line_total_cents": quote_item.line_total_cents,
                "sku": quote_item.sku_snapshot,
                "name": quote_item.name_snapshot,
                "color": quote_item.color_snapshot,
                "size": quote_item.size_snapshot,
                "offer_id": quote_item.offer_id,
            }
        )

    subtotal = sum(line["line_total_cents"] for line in priced_lines)
    shipping = 0
    total = subtotal + shipping

    if quote is not None and total != quote.total_cents:
        # 报价之后价格被改过。宁可让客户重新确认，也不能按新价默默扣款。
        raise PriceChangedDuringCheckout(
            "价格已变动，请重新报价",
            quoted_total_cents=quote.total_cents,
            actual_total_cents=total,
        )

    order = Order(
        order_no=next_order_no(db),
        idempotency_key=key,
        customer_id=customer.id,
        customer_phone=customer.phone,
        conversation_id=conversation_id,
        quote_id=quote.id if quote else None,
        status="pending_confirm",
        source="ai",
        subtotal_cents=subtotal,
        discount_total_cents=sum(
            (line["list_price_cents"] - line["unit_price_cents"]) * line["qty"]
            for line in priced_lines
        ),
        shipping_cents=shipping,
        total_cents=total,
        currency="CNY",
    )

    try:
        # SAVEPOINT：唯一索引冲突时只回滚这一段，外层事务仍然可用。
        # 直接让 IntegrityError 冒泡到最外层的话，Postgres 的事务会进入
        # aborted 状态，之后任何语句都会报 25P02。
        with db.begin_nested():
            db.add(order)
            db.flush()
    except IntegrityError:
        existing = _load_existing(db, key)
        if existing is None:
            raise  # 不是幂等键冲突，是别的约束 —— 不该被吞掉
        return existing, True

    # 扣库存放在建单之后：任何一行不够都会让整个事务回滚，订单不会残留。
    inventory_service.deduct_locked(
        db, [inventory_service.StockLine(l["variant_id"], l["qty"]) for l in priced_lines]
    )

    for line in priced_lines:
        db.add(
            OrderItem(
                order_id=order.id,
                variant_id=line["variant_id"],
                sku_snapshot=line["sku"],
                name_snapshot=line["name"],
                color_snapshot=line["color"],
                size_snapshot=line["size"],
                qty=line["qty"],
                list_price_cents=line["list_price_cents"],
                unit_price_cents=line["unit_price_cents"],
                floor_price_cents=line["floor_price_cents"],
                discount_pct=line["discount_pct"],
                line_total_cents=line["line_total_cents"],
                quote_item_offer_id=line["offer_id"],
            )
        )

    if quote is not None:
        quote_service.consume(db, quote, order.id)

    customer.total_orders = (customer.total_orders or 0) + 1
    customer.total_spent_cents = (customer.total_spent_cents or 0) + total

    db.flush()
    db.refresh(order)
    return order, False


def order_to_tool_payload(order: Order, deduplicated: bool) -> dict:
    """给模型看的订单形态。

    金额用服务端算好的数字，模型只负责转述。
    deduplicated 要显式告诉模型，它才能回一句
    「您刚才的订单已经生成了」而不是「下单成功」——
    客户连点两次时后者会让客户以为买了两单。
    """
    from app.core.money import to_yuan

    return {
        "ok": True,
        "data": {
            "order_no": order.order_no,
            "status": order.status,
            "total_cents": order.total_cents,
            "total_display": f"¥{to_yuan(order.total_cents):.2f}",
            "item_count": len(order.items),
            "items": [
                {
                    "sku": i.sku_snapshot,
                    "name": i.name_snapshot,
                    "color": i.color_snapshot,
                    "size": i.size_snapshot,
                    "qty": i.qty,
                    "unit_price_display": f"¥{to_yuan(i.unit_price_cents):.2f}",
                    "line_total_display": f"¥{to_yuan(i.line_total_cents):.2f}",
                }
                for i in order.items
            ],
            "deduplicated": deduplicated,
        },
    }


def order_summary_for_admin(order: Order) -> dict:
    from app.core.money import to_yuan

    return {
        "order_no": order.order_no,
        "status": order.status,
        "source": order.source,
        "customer_phone": order.customer_phone,
        "customer_name": order.customer.name if order.customer else None,
        "total_cents": order.total_cents,
        "total_display": f"¥{to_yuan(order.total_cents):.2f}",
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "sku": i.sku_snapshot,
                "name": i.name_snapshot,
                "color": i.color_snapshot,
                "size": i.size_snapshot,
                "qty": i.qty,
                "unit_price_display": f"¥{to_yuan(i.unit_price_cents):.2f}",
                # 最低价对照：后台能看到这一单离底线还有多远
                "floor_price_display": f"¥{to_yuan(i.floor_price_cents):.2f}",
                "line_total_display": f"¥{to_yuan(i.line_total_cents):.2f}",
            }
            for i in order.items
        ],
    }
