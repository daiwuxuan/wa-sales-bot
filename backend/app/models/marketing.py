"""推送审核与群发广告。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow


class PushRecommendation(Base, TimestampMixin):
    """推送审核页的推荐框。一个客户一条待审建议。"""

    __tablename__ = "push_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)

    interest: Mapped[str | None] = mapped_column(Text)          # 兴趣画像
    history_summary: Mapped[str | None] = mapped_column(Text)   # 历史购买摘要
    recommended_sku: Mapped[str | None] = mapped_column(String(64))
    recommended_variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id")
    )
    ai_copy: Mapped[str | None] = mapped_column(Text)           # AI 生成的话术

    # pending | sent | skipped | edited
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    edited_copy: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(lazy="selectin")  # noqa: F821


class MessageTemplate(Base, TimestampMixin):
    """消息模板。

    真实 WhatsApp 在 24 小时客服窗口外**只允许发送预审批模板**，
    自由文本会被拒。现在就把 wa_template_status 建好，
    二期填上 wa_template_name 就能直接用，业务代码不用改。
    """

    __tablename__ = "message_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8), default="zh_CN")
    body_template: Mapped[str] = mapped_column(Text)            # 含 {{1}} {{2}} 占位符
    variables_json: Mapped[str | None] = mapped_column(Text)

    wa_template_name: Mapped[str | None] = mapped_column(String(120))
    # none | pending | approved | rejected
    wa_template_status: Mapped[str] = mapped_column(String(16), default="none")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("message_templates.id"))
    message_text: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))

    # 分群条件：{"tiers": ["vip"], "last_order_within_days": 30, ...}
    audience_filter_json: Mapped[str | None] = mapped_column(Text)

    # draft | pending_approval | sending | paused | done
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    throttle_per_minute: Mapped[int] = mapped_column(Integer, default=60)

    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)

    created_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipients: Mapped[list[CampaignRecipient]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "customer_id", name="uq_campaign_customer"),
        # 复合索引已覆盖 campaign_id 前缀，不要再给单列加 index=True ——
        # 否则会生成一个完全冗余的索引，还可能撞名字。
        Index("ix_campaign_recipients_campaign_status", "campaign_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))

    rendered_body: Mapped[str | None] = mapped_column(Text)
    # pending | sent | delivered | read | failed | opted_out
    status: Mapped[str] = mapped_column(String(16), default="pending")
    wa_message_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")
