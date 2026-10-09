"""报价。

这是「AI 绝不能编造价格」的核心机制。

get_quote 不返回「价格计算公式」，而是返回规则引擎预先算好的**有限枚举**
（authorized offers）。每个档位带一个 offer_id 和一个算好的 unit_price_cents。
模型只能**选择并原样转述**，不能计算、不能加总、不能凑整 —— 因为它拿到的
本来就是一个封闭列表，没有任何可以运算的中间量。

下单时服务端按 offer_id 从 quote_items 读回单价重新校验，
所以模型在自然语言里说的价格和 DB 里记的价格永远不会脱钩。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Quote(Base, TimestampMixin):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )

    # 报价时的等级快照。事后客户升级了等级，也能还原当时为什么报这个价。
    customer_tier_code: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")

    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_total_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)

    # active -> consumed（已下单）| expired（过期）| cancelled
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    consumed_by_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))

    # 命中了哪些规则，审计用
    rules_applied_json: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    items: Mapped[list[QuoteItem]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", lazy="selectin"
    )


class QuoteItem(Base):
    """报价行 == 一个「已授权的报价选项」。"""

    __tablename__ = "quote_items"
    __table_args__ = (
        UniqueConstraint("quote_id", "offer_id", name="uq_quote_offer"),
        CheckConstraint("unit_price_cents >= 0", name="ck_quote_item_price_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)

    # 快照，防止商品改名后历史报价对不上
    sku_snapshot: Mapped[str] = mapped_column(String(64))
    name_snapshot: Mapped[str] = mapped_column(String(200))
    color_snapshot: Mapped[str] = mapped_column(String(64))
    size_snapshot: Mapped[str] = mapped_column(String(64))

    qty: Mapped[int] = mapped_column(Integer)
    list_price_cents: Mapped[int] = mapped_column(Integer)   # 原价
    unit_price_cents: Mapped[int] = mapped_column(Integer)   # 成交单价（已含折扣，已守最低价）
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    line_total_cents: Mapped[int] = mapped_column(Integer)

    # 最低价快照。留在库里有审计价值，但绝不进模型上下文。
    floor_price_cents: Mapped[int] = mapped_column(Integer)

    # ★ 模型唯一被允许引用的价格标识
    offer_id: Mapped[str] = mapped_column(String(24))

    quote: Mapped[Quote] = relationship(back_populates="items")
