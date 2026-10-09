"""客户与等级。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CustomerTier(Base, TimestampMixin):
    """客户等级。

    注意：折扣率的唯一权威是 pricing_service 读取的 discount_rules / 本表的
    default_discount_pct。get_customer 工具只返回等级名，不返回折扣率 ——
    两处都返回折扣率就等于两处可能不一致，而且会把定价权漏给模型。
    """

    __tablename__ = "customer_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    default_discount_pct: Mapped[int] = mapped_column(Integer, default=0)  # 0=无折扣
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # E.164 格式，不带 +，如 8613800138000
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    tier_id: Mapped[int | None] = mapped_column(ForeignKey("customer_tiers.id"), index=True)
    locale: Mapped[str] = mapped_column(String(8), default="zh")

    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    total_spent_cents: Mapped[int] = mapped_column(Integer, default=0)

    # 退订 / 拉黑。真实环境里不处理 STOP 关键词会被封号。
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)

    notes: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tier: Mapped[CustomerTier | None] = relationship(lazy="selectin")

    @property
    def tier_code(self) -> str:
        return self.tier.code if self.tier else "retail"
