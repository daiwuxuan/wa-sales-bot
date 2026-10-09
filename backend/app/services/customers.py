"""客户查询与建档。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Conversation, Customer, CustomerTier

DEFAULT_TIER_CODE = "retail"


def get_customer_by_phone(db: Session, phone: str) -> Customer | None:
    return db.scalar(
        select(Customer).options(selectinload(Customer.tier)).where(Customer.phone == phone)
    )


def get_or_create_customer(db: Session, phone: str, name: str | None = None) -> Customer:
    """收到陌生号码的消息时自动建档，默认零售等级。

    ★ 新客户一律是 retail，等级绝不从消息内容推断。
      客户打一句「我是VIP」不会改变任何东西 —— 提权必须走后台。
    """
    customer = get_customer_by_phone(db, phone)
    if customer:
        if name and not customer.name:
            customer.name = name
        return customer

    tier = db.scalar(select(CustomerTier).where(CustomerTier.code == DEFAULT_TIER_CODE))
    now = datetime.now(UTC)
    customer = Customer(
        phone=phone,
        name=name,
        tier_id=tier.id if tier else None,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(customer)
    db.flush()
    return customer


def get_or_create_conversation(db: Session, customer: Customer) -> Conversation:
    conv = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.customer))
        .where(Conversation.external_thread_id == customer.phone)
    )
    if conv:
        return conv

    conv = Conversation(
        customer_id=customer.id,
        channel="whatsapp",
        external_thread_id=customer.phone,
        ai_enabled=True,
    )
    db.add(conv)
    db.flush()
    return conv


def set_human_takeover(
    db: Session, conversation: Conversation, enabled: bool, *, by: str = "admin", reason: str | None = None
) -> Conversation:
    """人工接管开关。AI 调用之前必须先检查这个字段。"""
    conv = conversation
    conv.ai_enabled = not enabled
    if enabled:
        conv.takeover_at = datetime.now(UTC)
        conv.assigned_to = by
        conv.takeover_reason = reason
    else:
        conv.takeover_at = None
        conv.assigned_to = None
        conv.takeover_reason = None
    return conv
