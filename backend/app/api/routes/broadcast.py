"""群发广告。

★ 发送前必须经过审核（status 从 draft -> pending_approval -> sending）。
  这是刻意的：群发是不可撤回的对外动作，不该有一条「一键直接发」的路径。

★ 退订客户必须排除。真实环境里给退订客户群发会导致封号，
  这里虽然只是 Mock，规则照样执行 —— 免得接入真实 API 时才发现漏了。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models import (
    Campaign,
    CampaignRecipient,
    Conversation,
    Customer,
    MessageTemplate,
    utcnow,
)
from app.services import conversation as conversation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/campaigns", tags=["broadcast"])


class AudienceFilter(BaseModel):
    tiers: list[str] = Field(default_factory=list)
    min_total_orders: int = 0
    has_conversation: bool = True


class CampaignCreate(BaseModel):
    name: str
    message_text: str
    image_url: str | None = None
    template_id: int | None = None
    throttle_per_minute: int = 60
    audience: AudienceFilter = Field(default_factory=AudienceFilter)


@router.get("")
def list_campaigns(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Campaign).order_by(Campaign.created_at.desc()))
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "message_text": c.message_text,
                "image_url": c.image_url,
                "status": c.status,
                "total_targets": c.total_targets,
                "sent_count": c.sent_count,
                "failed_count": c.failed_count,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "finished_at": c.finished_at.isoformat() if c.finished_at else None,
            }
            for c in rows
        ]
    }


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(MessageTemplate).where(MessageTemplate.is_active.is_(True)))
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "category": t.category,
                "body_template": t.body_template,
                "wa_template_status": t.wa_template_status,
            }
            for t in rows
        ]
    }


def _build_audience(db: Session, audience: AudienceFilter) -> list[Customer]:
    stmt = select(Customer).options(selectinload(Customer.tier)).where(
        Customer.is_blacklisted.is_(False)  # ★ 退订/拉黑的永远排除
    )
    if audience.tiers:
        from app.models import CustomerTier

        stmt = stmt.join(CustomerTier, CustomerTier.id == Customer.tier_id).where(
            CustomerTier.code.in_(audience.tiers)
        )
    if audience.min_total_orders:
        stmt = stmt.where(Customer.total_orders >= audience.min_total_orders)

    customers = list(db.scalars(stmt))

    if audience.has_conversation:
        # 没有会话就发不出去（真实环境里也没有 24 小时客服窗口）
        with_conv = set(
            db.scalars(select(Conversation.customer_id))
        )
        customers = [c for c in customers if c.id in with_conv]

    return customers


@router.post("/preview")
def preview_audience(body: AudienceFilter, db: Session = Depends(get_db)) -> dict:
    """发送前先看命中多少人。"""
    customers = _build_audience(db, body)
    return {
        "count": len(customers),
        "sample": [
            {"phone": c.phone, "name": c.name, "tier": c.tier_code} for c in customers[:10]
        ],
    }


@router.post("")
def create_campaign(body: CampaignCreate, db: Session = Depends(get_db)) -> dict:
    customers = _build_audience(db, body.audience)
    if not customers:
        raise HTTPException(status_code=400, detail="没有命中任何客户")

    campaign = Campaign(
        name=body.name,
        message_text=body.message_text,
        image_url=body.image_url,
        template_id=body.template_id,
        throttle_per_minute=body.throttle_per_minute,
        audience_filter_json=json.dumps(body.audience.model_dump(), ensure_ascii=False),
        status="pending_approval",  # ★ 创建后必须经过审核才会发
        total_targets=len(customers),
    )
    db.add(campaign)
    db.flush()

    for customer in customers:
        conversation = db.scalar(
            select(Conversation).where(Conversation.customer_id == customer.id)
        )
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                customer_id=customer.id,
                conversation_id=conversation.id if conversation else None,
                rendered_body=body.message_text,
                status="pending",
            )
        )

    db.commit()
    return {"ok": True, "id": campaign.id, "total_targets": len(customers), "status": campaign.status}


@router.post("/{campaign_id}/approve")
def approve_campaign(campaign_id: int, by: str = "admin", db: Session = Depends(get_db)) -> dict:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    if campaign.status != "pending_approval":
        raise HTTPException(status_code=400, detail=f"当前状态 {campaign.status} 不能批准")

    campaign.status = "sending"
    campaign.approved_by = by
    campaign.started_at = utcnow()
    db.commit()
    return {"ok": True, "id": campaign_id, "status": "sending"}


@router.post("/{campaign_id}/send")
def send_campaign(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    """执行发送。

    Mock 阶段一次性发完。真实环境必须按 throttle_per_minute 节流，
    否则会被 WhatsApp 判定为垃圾消息直接封号 —— 这个字段已经存在表里了。
    """
    campaign = db.scalar(
        select(Campaign)
        .options(selectinload(Campaign.recipients))
        .where(Campaign.id == campaign_id)
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    if campaign.status != "sending":
        raise HTTPException(status_code=400, detail="活动未处于发送状态，请先批准")

    sent = failed = 0

    for recipient in campaign.recipients:
        if recipient.status != "pending":
            continue

        customer = db.get(Customer, recipient.customer_id)

        if customer is None or customer.is_blacklisted:
            recipient.status = "opted_out"
            recipient.error = "客户已退订或不存在"
            failed += 1
            continue

        try:
            conversation_service.deliver(
                conversation_id=recipient.conversation_id,
                customer_id=recipient.customer_id,
                phone=customer.phone,
                text=recipient.rendered_body,
                image_url=campaign.image_url,
                caption=campaign.name if campaign.image_url else None,
                sent_by="system",
            )
            recipient.status = "sent"
            recipient.sent_at = utcnow()
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("群发失败 customer=%s", recipient.customer_id)
            recipient.status = "failed"
            recipient.error = str(exc)[:200]
            failed += 1

    campaign.sent_count = sent
    campaign.failed_count = failed
    campaign.status = "done"
    campaign.finished_at = utcnow()
    db.commit()

    return {"ok": True, "id": campaign_id, "sent": sent, "failed": failed, "status": "done"}


@router.get("/{campaign_id}/recipients")
def campaign_recipients(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(CampaignRecipient)
        .options(selectinload(CampaignRecipient.campaign))
        .where(CampaignRecipient.campaign_id == campaign_id)
        .limit(500)
    )
    items = []
    for r in rows:
        customer = db.get(Customer, r.customer_id)
        items.append(
            {
                "id": r.id,
                "phone": customer.phone if customer else None,
                "name": customer.name if customer else None,
                "status": r.status,
                "error": r.error,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            }
        )
    return {"items": items, "count": len(items)}


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(CampaignRecipient.status, func.count()).group_by(CampaignRecipient.status)
    ).all()
    return {"by_status": {r[0]: int(r[1]) for r in rows}}
