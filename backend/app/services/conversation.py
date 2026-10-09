"""入站消息处理。

把通道层、去重、接管判断、AI 编排、出站落库串成一条线。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict

from sqlalchemy.exc import IntegrityError

from app.ai.factory import get_llm_client
from app.ai.orchestrator import run_turn
from app.channels import mock_channel
from app.channels.base import InboundMessage, OutboundMessage
from app.core.database import session_scope
from app.models import ChatLog, Conversation, utcnow
from app.services import customers as customer_service

logger = logging.getLogger(__name__)

# 同一会话的消息必须串行处理。
# 客户连发「有黑色XL吗」和「要了」两条消息，如果并发进入会起两个 AI turn，
# 可能生成两张订单、或者第二轮的上下文里看不到第一条的结果。
# 单进程 uvicorn 下进程内锁就够了；多 worker 要换成 Redis 锁。
_CONVERSATION_LOCKS: dict[int, threading.Lock] = defaultdict(threading.Lock)
_LOCKS_GUARD = threading.Lock()


def _lock_for(conversation_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _CONVERSATION_LOCKS[conversation_id]


def log_message(
    *,
    conversation_id: int | None,
    customer_id: int | None,
    direction: str,
    phone: str,
    body: str | None,
    message_type: str = "text",
    media_url: str | None = None,
    wa_message_id: str | None = None,
    sent_by: str = "system",
) -> int:
    with session_scope() as db:
        row = ChatLog(
            conversation_id=conversation_id,
            customer_id=customer_id,
            direction=direction,
            message_type=message_type,
            phone=phone,
            body=body,
            media_url=media_url,
            wa_message_id=wa_message_id,
            sent_by=sent_by,
            created_at=utcnow(),
        )
        db.add(row)
        db.flush()
        return row.id


def deliver(
    *,
    conversation_id: int | None,
    customer_id: int | None,
    phone: str,
    text: str | None = None,
    image_url: str | None = None,
    caption: str | None = None,
    sent_by: str = "ai",
) -> None:
    """落库 + 发送一条出站消息。

    ★ 顺序是「先写库，再发送」，不能反过来。
      先发后写的话，一旦写库失败，客户收到了消息但系统里没有任何记录 ——
      之后再查这段对话就是缺失的，人工接管时看不到上下文。

    AI 回复、人工回复、推送审核、群发都走这一个函数，
    保证四条路径的落库行为完全一致。
    """
    if text is not None:
        log_message(
            conversation_id=conversation_id,
            customer_id=customer_id,
            direction="out",
            phone=phone,
            body=text,
            sent_by=sent_by,
        )
        mock_channel.send(
            OutboundMessage(phone=phone, type="text", text=text, sent_by=sent_by)
        )

    if image_url:
        log_message(
            conversation_id=conversation_id,
            customer_id=customer_id,
            direction="out",
            phone=phone,
            body=caption,
            message_type="image",
            media_url=image_url,
            sent_by=sent_by,
        )
        mock_channel.send(
            OutboundMessage(
                phone=phone, type="image", image_url=image_url,
                caption=caption, sent_by=sent_by,
            )
        )


def _record_inbound_once(
    *, conversation_id: int, customer_id: int, inbound: InboundMessage
) -> int | None:
    """落库入站消息。撞上 wa_message_id 唯一索引说明是重投，返回 None。

    WhatsApp 在你超时或返回非 2xx 时会重投同一条消息。
    不在这里拦住的话，会触发第二整个 AI turn —— 客户会收到两条回复，
    如果那条消息是「要了」，还会多出一张订单。
    """
    try:
        return log_message(
            conversation_id=conversation_id,
            customer_id=customer_id,
            direction="in",
            phone=inbound.phone,
            body=inbound.text,
            message_type=inbound.type,
            media_url=inbound.image_url,
            wa_message_id=inbound.wa_message_id,
            sent_by="system",
        )
    except IntegrityError:
        logger.info("忽略重投消息 wa_message_id=%s", inbound.wa_message_id)
        return None


def handle_inbound(inbound: InboundMessage) -> dict:
    """处理一条入站消息，返回给 webhook 的响应体。"""
    # 1) 建档 + 取会话（短事务）
    with session_scope() as db:
        customer = customer_service.get_or_create_customer(
            db, inbound.phone, inbound.profile_name
        )
        conversation = customer_service.get_or_create_conversation(db, customer)
        conversation.last_inbound_at = inbound.timestamp
        customer.last_seen_at = utcnow()
        conversation_id = conversation.id
        customer_id = customer.id
        ai_enabled = conversation.ai_enabled

    # 2) 去重 + 落库入站消息
    inbound_log_id = _record_inbound_once(
        conversation_id=conversation_id, customer_id=customer_id, inbound=inbound
    )
    if inbound_log_id is None:
        return {"status": "accepted", "deduplicated": True}

    # 3) 人工接管中 —— 不调用 AI。
    #    必须在调模型**之前**判断，放进循环里就变成「已经烧了 token 才发现不该回」。
    if not ai_enabled:
        return {
            "status": "accepted",
            "deduplicated": False,
            "ai_enabled": False,
            "note": "会话处于人工接管中，AI 未介入",
        }

    # 4) 同一会话串行
    with _lock_for(conversation_id):
        # 重新确认接管状态：人工可能在排队等待期间刚接管了这个会话
        with session_scope() as db:
            fresh = db.get(Conversation, conversation_id)
            if fresh is None or not fresh.ai_enabled:
                return {"status": "accepted", "deduplicated": False, "ai_enabled": False}

        result = run_turn(
            provider=get_llm_client(),
            conversation_id=conversation_id,
            customer_id=customer_id,
            customer_phone=inbound.phone,
            inbound_text=inbound.text or "",
            inbound_chat_log_id=inbound_log_id,
        )

        # 5) 出站。先文字后图片 —— 客户先读到报价再看到图，顺序更自然。
        deliver(
            conversation_id=conversation_id,
            customer_id=customer_id,
            phone=inbound.phone,
            text=result.text,
            sent_by="ai",
        )
        for image in result.outbound_images:
            deliver(
                conversation_id=conversation_id,
                customer_id=customer_id,
                phone=inbound.phone,
                image_url=image["url"],
                caption=image.get("caption"),
                sent_by="ai",
            )

    return {
        "status": "ok",
        "deduplicated": False,
        "ai_enabled": True,
        "conversation_id": conversation_id,
        "turn_id": result.turn_id,
        "turn_status": result.status,
        "reply_text": result.text,
        "escalated": result.escalated,
        "guard": {"violations": result.guard_violations},
        "steps": [t.to_step_payload() for t in result.traces],
    }


def set_takeover(conversation_id: int, enabled: bool, *, by: str = "admin",
                 reason: str | None = None) -> dict:
    with session_scope() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            return {"ok": False, "error_code": "conversation_not_found"}
        customer_service.set_human_takeover(
            db, conversation, enabled, by=by, reason=reason
        )
        return {"ok": True, "conversation_id": conversation_id, "ai_enabled": conversation.ai_enabled}


def human_reply(conversation_id: int, text: str, *, by: str = "admin") -> dict:
    """人工坐席发消息。和 AI 出站走同一条路。"""
    with session_scope() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            return {"ok": False, "error_code": "conversation_not_found"}
        phone = conversation.external_thread_id
        customer_id = conversation.customer_id

    deliver(
        conversation_id=conversation_id,
        customer_id=customer_id,
        phone=phone,
        text=text,
        sent_by="human",
    )
    return {"ok": True, "sent": True, "by": by}
