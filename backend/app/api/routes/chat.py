"""会话、消息日志、人工接管。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.factory import current_mode
from app.core.database import get_db
from app.models import AiTurn, ChatLog, Conversation, Customer, ToolCallLog
from app.services import conversation as conversation_service

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/llm/mode")
def llm_mode() -> dict:
    """当前用的是真实模型还是 Mock。

    探活失败自动降级时这里会显示 mock + 原因，避免你以为在用 GPT-4o。
    """
    return current_mode()


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200),
) -> dict:
    rows = db.scalars(
        select(Conversation)
        .options(selectinload(Conversation.customer))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": c.id,
                "phone": c.external_thread_id,
                "customer_name": c.customer.name if c.customer else None,
                "tier": c.customer.tier_code if c.customer else None,
                "ai_enabled": c.ai_enabled,
                "assigned_to": c.assigned_to,
                "takeover_reason": c.takeover_reason,
                "unread_count": c.unread_count,
                "last_inbound_at": c.last_inbound_at.isoformat() if c.last_inbound_at else None,
            }
            for c in rows
        ]
    }


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, le=500),
) -> dict:
    rows = db.scalars(
        select(ChatLog)
        .where(ChatLog.conversation_id == conversation_id)
        .order_by(ChatLog.created_at.asc(), ChatLog.id.asc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": m.id,
                "direction": m.direction,
                "type": m.message_type,
                "body": m.body,
                "media_url": m.media_url,
                "sent_by": m.sent_by,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }


class TakeoverBody(BaseModel):
    enabled: bool
    by: str = "admin"
    reason: str | None = None


@router.patch("/conversations/{conversation_id}/takeover")
def set_takeover(conversation_id: int, body: TakeoverBody) -> dict:
    result = conversation_service.set_takeover(
        conversation_id, body.enabled, by=body.by, reason=body.reason
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="会话不存在")
    return result


class HumanReplyBody(BaseModel):
    text: str
    by: str = "admin"


@router.post("/conversations/{conversation_id}/reply")
def human_reply(conversation_id: int, body: HumanReplyBody) -> dict:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="回复内容不能为空")
    result = conversation_service.human_reply(conversation_id, body.text, by=body.by)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="会话不存在")
    return result


@router.get("/ai/turns/{turn_id}")
def turn_detail(turn_id: str, db: Session = Depends(get_db)) -> dict:
    """回放一次 AI 处理过程。

    这是「所有 AI 回复和工具调用要记录日志」的兑现处 ——
    后台订单详情里的「AI 处理轨迹」直接渲染这个。
    """
    turn = db.get(AiTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="turn 不存在")

    steps = db.scalars(
        select(ToolCallLog)
        .where(ToolCallLog.turn_id == turn_id)
        .order_by(ToolCallLog.step_index, ToolCallLog.id)
    )

    return {
        "turn_id": turn.id,
        "conversation_id": turn.conversation_id,
        "llm_mode": turn.llm_mode,
        "model": turn.model,
        "status": turn.status,
        "final_text": turn.final_text,
        "guard_violations": json.loads(turn.guard_violations_json or "[]"),
        "allowed_numbers": json.loads(turn.allowed_numbers_json or "[]"),
        "tokens": {"input": turn.prompt_tokens, "output": turn.completion_tokens},
        "latency_ms": turn.latency_ms,
        "error": turn.error,
        "created_at": turn.created_at.isoformat() if turn.created_at else None,
        "steps": [
            {
                "step": s.step_index,
                "tool": s.tool_name,
                "arguments": json.loads(s.arguments_json or "{}"),
                "ok": s.result_ok,
                "error_code": s.error_code,
                "duration_ms": s.duration_ms,
                "result": json.loads(s.result_json or "{}"),
            }
            for s in steps
        ],
    }


@router.get("/conversations/{conversation_id}/turns")
def list_turns(conversation_id: int, db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(AiTurn)
        .where(AiTurn.conversation_id == conversation_id)
        .order_by(AiTurn.created_at.desc())
        .limit(50)
    )
    return {
        "items": [
            {
                "turn_id": t.id,
                "status": t.status,
                "llm_mode": t.llm_mode,
                "final_text": t.final_text,
                "latency_ms": t.latency_ms,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ]
    }


@router.get("/chat/logs")
def chat_logs(
    phone: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ChatLog).order_by(ChatLog.created_at.desc(), ChatLog.id.desc()).limit(limit)
    if phone:
        stmt = stmt.where(ChatLog.phone == phone)

    rows = db.scalars(stmt)
    return {
        "items": [
            {
                "id": m.id,
                "phone": m.phone,
                "direction": m.direction,
                "type": m.message_type,
                "body": m.body,
                "media_url": m.media_url,
                "sent_by": m.sent_by,
                "wa_message_id": m.wa_message_id,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }


@router.get("/customers")
def list_customers(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Customer).options(selectinload(Customer.tier)).order_by(Customer.id))
    return {
        "items": [
            {
                "id": c.id,
                "phone": c.phone,
                "name": c.name,
                "tier": c.tier_code,
                "tier_name": c.tier.name if c.tier else None,
                "total_orders": c.total_orders,
                "total_spent_display": f"¥{(c.total_spent_cents or 0) / 100:.2f}",
                "is_blacklisted": c.is_blacklisted,
            }
            for c in rows
        ]
    }
