"""报价单的建立、校验与消费。

报价是「一次购买意图」的凭证：它把客户当时看到的每一个数字钉死在库里，
下单时按 offer_id 取回。所以模型在聊天里说的价格和订单里的价格
不可能脱钩 —— 它们本来就来自同一行数据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.errors import InvalidArgument, QuoteAlreadyConsumed, QuoteExpired
from app.core.ids import next_quote_no
from app.models import Customer, CustomerTier, ProductVariant, Quote, QuoteItem
from app.services.pricing import Offer


@dataclass(frozen=True)
class QuoteEntry:
    """一个待入报价的档位：变体 + 规则引擎算好的 offer。"""

    variant: ProductVariant
    offer: Offer


def create_quote(
    db: Session,
    *,
    customer: Customer,
    tier: CustomerTier | None,
    conversation_id: int | None,
    entries: list[QuoteEntry],
) -> Quote:
    """把**已经算好的** offer 落成报价单。

    ★ 刻意接收算好的 Offer，而不是 (变体, 数量) 再算一遍。
      之前那样写会算两次：工具层算一次给模型看，这里再算一次入库。
      两次计算一旦有任何差异（改了折扣规则、并发改了价），
      模型看到的价和库里的价就对不上了 —— 而且是静默的。

      现在只有一次计算，offer_id 也直接沿用，漂移在结构上不可能发生。
    """
    if not entries:
        raise InvalidArgument("报价至少要有一个商品")

    now = datetime.now(UTC)
    quote = Quote(
        quote_no=next_quote_no(db),
        customer_id=customer.id,
        conversation_id=conversation_id,
        customer_tier_code=tier.code if tier else "retail",
        currency="CNY",
        status="active",
        expires_at=now + timedelta(minutes=settings.quote_ttl_minutes),
    )
    db.add(quote)
    db.flush()

    subtotal = 0
    rules: list[str] = []
    seen_offer_ids: set[str] = set()

    for entry in entries:
        variant, offer = entry.variant, entry.offer

        if offer.offer_id in seen_offer_ids:
            raise InvalidArgument("报价里出现了重复的 offer_id", offer_id=offer.offer_id)
        seen_offer_ids.add(offer.offer_id)

        db.add(
            QuoteItem(
                quote_id=quote.id,
                variant_id=variant.id,
                sku_snapshot=variant.product.sku if variant.product else variant.variant_sku,
                name_snapshot=variant.product.name if variant.product else "",
                color_snapshot=variant.color_label,
                size_snapshot=variant.size_label,
                qty=offer.qty,
                list_price_cents=offer.list_price_cents,
                unit_price_cents=offer.unit_price_cents,
                discount_pct=offer.discount_pct,
                line_total_cents=offer.line_total_cents,
                floor_price_cents=variant.floor_price_cents,
                offer_id=offer.offer_id,
            )
        )

        subtotal += offer.line_total_cents
        if offer.discount_pct:
            rules.append(
                f"tier={quote.customer_tier_code} qty={offer.qty} -{offer.discount_pct}%"
            )
        if offer.floor_hit:
            # 触发最低价保护要留痕：说明客户拿到的价格不是按折扣算出来的，
            # 而是被底线挡住了，事后对账时能解释清楚。
            rules.append(f"variant={variant.id} floor_guard_applied")

    quote.subtotal_cents = subtotal
    db.flush()  # 让 quote.items 可用

    quote.discount_total_cents = sum(
        (i.list_price_cents - i.unit_price_cents) * i.qty for i in quote.items
    )
    quote.total_cents = subtotal + 0  # 一期包邮
    quote.rules_applied_json = json.dumps(rules, ensure_ascii=False)

    db.flush()
    return quote


def load_quote(db: Session, quote_no: str) -> Quote | None:
    return db.scalar(
        select(Quote).options(selectinload(Quote.items)).where(Quote.quote_no == quote_no)
    )


def validate_for_checkout(
    db: Session, quote: Quote, *, customer: Customer, conversation_id: int | None
) -> Quote:
    """下单前的三道校验。任一不过都必须重新报价。

    这是防「模型跳过确认直接下单」和「拿别人的报价下单」的关键。
    """
    if quote.customer_id != customer.id:
        raise InvalidArgument("该报价不属于当前客户", quote_no=quote.quote_no)

    if conversation_id is not None and quote.conversation_id not in (None, conversation_id):
        raise InvalidArgument("该报价不属于当前会话", quote_no=quote.quote_no)

    if quote.status == "consumed":
        raise QuoteAlreadyConsumed("该报价已经下过单了", quote_no=quote.quote_no)

    if quote.status != "active":
        raise QuoteExpired("该报价已失效", quote_no=quote.quote_no, status=quote.status)

    expires_at = quote.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is not None and expires_at < datetime.now(UTC):
        quote.status = "expired"
        db.flush()
        raise QuoteExpired("该报价已过期，请重新报价", quote_no=quote.quote_no)

    return quote


def consume(db: Session, quote: Quote, order_id: int) -> None:
    quote.status = "consumed"
    quote.consumed_by_order_id = order_id
    db.flush()
