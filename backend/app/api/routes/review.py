"""推送审核。

每个客户一个推荐框，人工点「发送 / 不发送 / 编辑后发送」。
AI 只负责起草，决策权在人手上 —— 这是对外发消息的默认形态，
不该有「AI 直接群发」的路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import get_db
from app.models import (
    Conversation,
    Customer,
    Order,
    OrderItem,
    ProductVariant,
    PushRecommendation,
    utcnow,
)
from app.services import conversation as conversation_service

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/queue")
def queue(db: Session = Depends(get_db), status: str = "pending") -> dict:
    stmt = (
        select(PushRecommendation)
        .options(selectinload(PushRecommendation.customer))
        .order_by(PushRecommendation.created_at.desc())
    )
    if status != "all":
        stmt = stmt.where(PushRecommendation.status == status)

    items = []
    for rec in db.scalars(stmt):
        variant = (
            db.get(ProductVariant, rec.recommended_variant_id)
            if rec.recommended_variant_id
            else None
        )
        image_key = variant.product.image_url if variant and variant.product else None
        items.append(
            {
                "id": rec.id,
                "customer_id": rec.customer_id,
                "customer_name": rec.customer.name if rec.customer else None,
                "phone": rec.customer.phone if rec.customer else None,
                "tier": rec.customer.tier_code if rec.customer else None,
                "interest": rec.interest,
                "history_summary": rec.history_summary,
                "recommended_sku": rec.recommended_sku,
                "recommended_name": variant.product.name if variant and variant.product else None,
                "image_url": settings.public_url_for(image_key),
                "ai_copy": rec.ai_copy,
                "edited_copy": rec.edited_copy,
                "status": rec.status,
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
            }
        )
    return {"items": items}


class DecisionBody(BaseModel):
    edited_copy: str | None = None
    by: str = "admin"


def _decide(db: Session, rec_id: int, action: str, edited_copy: str | None, by: str) -> dict:
    rec = db.scalar(
        select(PushRecommendation)
        .options(selectinload(PushRecommendation.customer))
        .where(PushRecommendation.id == rec_id)
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="推荐不存在")

    rec.decided_by = by
    rec.decided_at = utcnow()

    if action == "skip":
        rec.status = "skipped"
        db.commit()
        return {"ok": True, "status": "skipped"}

    customer = db.get(Customer, rec.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")

    conversation = db.scalar(
        select(Conversation).where(Conversation.customer_id == customer.id)
    )
    if conversation is None:
        raise HTTPException(status_code=400, detail="该客户还没有会话，无法发送")

    body = edited_copy if action == "edit" else rec.ai_copy
    if not body:
        raise HTTPException(status_code=400, detail="发送内容为空")

    if action == "edit":
        rec.edited_copy = edited_copy
        rec.status = "edited"
    else:
        rec.status = "sent"

    variant = (
        db.get(ProductVariant, rec.recommended_variant_id)
        if rec.recommended_variant_id
        else None
    )
    image_key = variant.product.image_url if variant and variant.product else None

    db.commit()

    # AI 回复、人工回复、推送审核走同一个出口函数，
    # 保证「先写库再发送」这条规矩不会在某条路径上被漏掉。
    conversation_service.deliver(
        conversation_id=conversation.id,
        customer_id=customer.id,
        phone=customer.phone,
        text=body,
        image_url=settings.public_url_for(image_key) if image_key else None,
        caption=variant.product.name if variant and variant.product else None,
        sent_by="human",
    )

    return {"ok": True, "status": rec.status, "sent_body": body}


@router.post("/{rec_id}/approve")
def approve(rec_id: int, body: DecisionBody, db: Session = Depends(get_db)) -> dict:
    return _decide(db, rec_id, "approve", None, body.by)


@router.post("/{rec_id}/skip")
def skip(rec_id: int, body: DecisionBody, db: Session = Depends(get_db)) -> dict:
    return _decide(db, rec_id, "skip", None, body.by)


@router.post("/{rec_id}/edit")
def edit(rec_id: int, body: DecisionBody, db: Session = Depends(get_db)) -> dict:
    if not body.edited_copy:
        raise HTTPException(status_code=400, detail="编辑后内容不能为空")
    return _decide(db, rec_id, "edit", body.edited_copy, body.by)


@router.post("/generate")
def generate(db: Session = Depends(get_db), limit: int = 10) -> dict:
    """为有购买历史的客户批量生成推荐。

    兴趣和历史购买都是 SQL 查出来的事实，AI 只负责把它们组织成话术。
    """
    created: list[dict] = []

    customers = list(
        db.scalars(
            select(Customer)
            .options(selectinload(Customer.tier))
            .where(Customer.is_blacklisted.is_(False), Customer.total_orders > 0)
            .limit(limit)
        )
    )

    for customer in customers:
        existing = db.scalar(
            select(PushRecommendation).where(
                PushRecommendation.customer_id == customer.id,
                PushRecommendation.status == "pending",
            )
        )
        if existing:
            continue

        # 历史购买：真实 SQL 聚合
        history = db.execute(
            select(
                OrderItem.sku_snapshot,
                OrderItem.name_snapshot,
                OrderItem.color_snapshot,
                OrderItem.size_snapshot,
                OrderItem.qty,
                Order.order_no,
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.customer_id == customer.id, Order.status != "cancelled")
            .order_by(Order.created_at.desc())
            .limit(5)
        ).all()

        if not history:
            continue

        bought_skus = {h.sku_snapshot for h in history}
        # 推荐同款未购的变体；没有就推库存最多的款
        candidate = db.scalar(
            select(ProductVariant)
            .options(selectinload(ProductVariant.product))
            .join(ProductVariant.product)
            .where(ProductVariant.is_active.is_(True), ProductVariant.sku.not_in(bought_skus))
            .limit(1)
        )

        history_text = "、".join(
            f"{h.name_snapshot} {h.color_snapshot}{h.size_snapshot} ×{h.qty}" for h in history
        )
        interest = "、".join({h.name_snapshot.split(" ")[0] for h in history})

        rec = PushRecommendation(
            customer_id=customer.id,
            interest=f"偏好：{interest}" if interest else None,
            history_summary=history_text,
            recommended_sku=candidate.product.sku if candidate and candidate.product else None,
            recommended_variant_id=candidate.id if candidate else None,
            ai_copy=None,
            status="pending",
        )
        db.add(rec)
        db.flush()

        rec.ai_copy = _draft_copy(customer, history_text, candidate)
        created.append({"id": rec.id, "customer_id": customer.id})

    db.commit()
    return {"created": len(created), "items": created}


def _draft_copy(customer: Customer, history_text: str, candidate) -> str:
    """起草话术。

    ★ 刻意不走模型。这段文案里要提到商品名和客户的购买历史，
      一旦让模型自由发挥，它很容易顺手编出价格或库存。
      没有价格的话术用模板生成就够了，也更可控。
    """
    name = customer.name or "您好"
    tier_word = customer.tier.name if customer.tier else ""

    if candidate and candidate.product:
        product_name = candidate.product.name
        return (
            f"{name}，看到您之前买过{history_text.split('、')[0]}，"
            f"我们新到的{product_name}应该也会喜欢，需要的话我发图片给您看看？"
        )
    return f"{name}，最近到了一批新品，{tier_word}可以优先看，有兴趣我发您几张图？"


@router.post("/seed-demo")
def seed_demo(db: Session = Depends(get_db)) -> dict:
    """给演示用：直接造几条待审推荐，不用先跑真实下单。"""
    customers = list(db.scalars(select(Customer).limit(5)))
    variants = list(db.scalars(select(ProductVariant).limit(5)))

    created = 0
    for index, customer in enumerate(customers):
        existing = db.scalar(
            select(PushRecommendation).where(
                PushRecommendation.customer_id == customer.id,
                PushRecommendation.status == "pending",
            )
        )
        if existing:
            continue

        variant = variants[index % len(variants)] if variants else None
        product_name = variant.product.name if variant and variant.product else "新品"
        db.add(
            PushRecommendation(
                customer_id=customer.id,
                interest="偏好：上衣、基础款",
                history_summary=f"{product_name} 黑色L ×1",
                recommended_sku=variant.product.sku if variant and variant.product else None,
                recommended_variant_id=variant.id if variant else None,
                ai_copy=(
                    f"{customer.name or '您好'}，您上次看的款我们补货了，"
                    f"新到的{product_name}也很适合您，发图给您看看？"
                ),
                status="pending",
            )
        )
        created += 1

    db.commit()
    return {"created": created}


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(PushRecommendation.status, func.count()).group_by(PushRecommendation.status)
    ).all()
    return {"by_status": {r[0]: int(r[1]) for r in rows}}
