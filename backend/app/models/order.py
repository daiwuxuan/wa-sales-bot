"""订单。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        Index("ix_orders_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # ★ 幂等仲裁者。并发下唯一索引保证只有一条能插进去。
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    customer_phone: Mapped[str] = mapped_column(String(32))  # 快照

    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))

    # pending_confirm | confirmed | paid | shipped | completed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending_confirm", index=True)

    # ai | manual | mock | broadcast
    source: Mapped[str] = mapped_column(String(16), default="ai")

    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_total_cents: Mapped[int] = mapped_column(Integer, default=0)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")

    note: Mapped[str | None] = mapped_column(Text)

    # 幂等命中时，指向真正那张单
    deduplicated_from_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    customer: Mapped["Customer"] = relationship(lazy="selectin")  # noqa: F821


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        # ★ 低于最低价的订单行物理上写不进库。
        #   人工特批走 floor_price_cents=0 的专用路径，不会绕过这条检查。
        CheckConstraint(
            "unit_price_cents >= floor_price_cents", name="ck_order_item_above_floor"
        ),
        CheckConstraint("qty > 0", name="ck_order_item_qty_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)

    sku_snapshot: Mapped[str] = mapped_column(String(64))
    name_snapshot: Mapped[str] = mapped_column(String(200))
    color_snapshot: Mapped[str] = mapped_column(String(64))
    size_snapshot: Mapped[str] = mapped_column(String(64))

    qty: Mapped[int] = mapped_column(Integer)
    list_price_cents: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    floor_price_cents: Mapped[int] = mapped_column(Integer)
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    line_total_cents: Mapped[int] = mapped_column(Integer)

    # 溯源：这一行来自哪个报价选项
    quote_item_offer_id: Mapped[str | None] = mapped_column(String(24))

    order: Mapped[Order] = relationship(back_populates="items")
