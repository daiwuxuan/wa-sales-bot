"""Mock 通道。

收：接受两种请求体，都规范化成 InboundMessage
    · Cloud API 原生信封（entry/changes/value/messages）
    · 开发用的扁平信封（{phone, text}）
发：不真的发任何东西，只把消息写进 chat_logs 和内存 outbox，
    供模拟器页面和自动化测试拉取。

★ 出站消息渲染用的是 OutboundMessage.render_cloud_payload()，
  和真实通道完全一致。所以模拟器里看到的 JSON，
  将来原样就是发给 Meta 的请求体。
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import UTC, datetime

from app.channels.base import (
    InboundMessage,
    OutboundMessage,
    _new_wamid,
    parse_cloud_envelope,
)

# 演示期的发件箱。进程重启即清空 —— 持久记录在 chat_logs 里。
_OUTBOX: deque[dict] = deque(maxlen=500)
_OUTBOX_LOCK = threading.Lock()


def parse_inbound(raw: dict) -> InboundMessage | None:
    """把两种请求体都规范化成 InboundMessage。"""
    if "entry" in raw:
        return parse_cloud_envelope(raw)
    return _parse_flat_envelope(raw)


def _parse_flat_envelope(raw: dict) -> InboundMessage | None:
    phone = raw.get("phone") or raw.get("from")
    if not phone:
        return None

    msg_type = raw.get("type") or ("image" if raw.get("image_url") else "text")
    return InboundMessage(
        phone=str(phone),
        type=msg_type,
        text=raw.get("text"),
        image_url=raw.get("image_url"),
        wa_message_id=raw.get("message_id") or raw.get("wa_message_id") or _new_wamid(),
        profile_name=raw.get("profile_name"),
        raw=raw,
    )


def send(message: OutboundMessage) -> dict:
    """Mock 发送：只入 outbox，不做任何网络请求。"""
    payload = message.render_cloud_payload()
    entry = {
        "wa_message_id": _new_wamid(),
        "to": message.phone,
        "sent_by": message.sent_by,
        "payload": payload,
        "sent_at": datetime.now(UTC).isoformat(),
    }
    with _OUTBOX_LOCK:
        _OUTBOX.append(entry)
    return entry


def drain_outbox(since: int = 0) -> list[dict]:
    """取出发件箱里第 since 条之后的消息。模拟器页轮询用。"""
    with _OUTBOX_LOCK:
        items = list(_OUTBOX)
    return items[since:]
