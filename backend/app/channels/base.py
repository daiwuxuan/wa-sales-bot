"""通道抽象。

InboundMessage / OutboundMessage 是**规范化**后的内部模型。
下游业务代码只认这两个类，不认识任何 WhatsApp 专有的信封格式。

将来换成真实 Cloud API 时，需要改的只有：
  · whatsapp_cloud.py 里的 parse_inbound（真实信封 -> InboundMessage）
  · 签名校验
业务代码零改动。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass
class InboundMessage:
    phone: str
    type: str                       # text | image
    text: str | None = None
    image_url: str | None = None
    wa_message_id: str | None = None
    profile_name: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict | None = None


@dataclass
class OutboundMessage:
    phone: str
    type: str                       # text | image
    text: str | None = None
    image_url: str | None = None
    caption: str | None = None
    sent_by: str = "ai"             # ai | human | system

    def render_cloud_payload(self) -> dict:
        """渲染成 Cloud API /messages 的请求体。

        Mock 通道也走这个方法 —— 两个通道产出同一个 dict，
        将来切换时不需要任何格式转换。
        """
        if self.type == "image":
            payload: dict = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": self.phone,
                "type": "image",
                "image": {"link": self.image_url},
            }
            if self.caption:
                payload["image"]["caption"] = self.caption
            return payload

        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.phone,
            "type": "text",
            "text": {"preview_url": False, "body": self.text or ""},
        }


class Channel(Protocol):
    def parse_inbound(self, raw: dict) -> InboundMessage | None: ...
    def send(self, message: OutboundMessage) -> dict: ...


def _new_wamid() -> str:
    return f"wamid.mock.{uuid.uuid4().hex[:20]}"


def parse_cloud_envelope(raw: dict) -> InboundMessage | None:
    """解析 Cloud API 的原生信封。

    放在 base 里而不是某个具体通道里，因为 Mock 通道和真实通道
    用的是同一套解析 —— 真实接入时直接复用，不需要重写一遍。
    """
    try:
        value = raw["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    messages = value.get("messages") or []
    if not messages:
        return None  # 状态回执（delivered/read）等，忽略

    message = messages[0]
    msg_type = message.get("type", "text")

    contacts = value.get("contacts") or []
    profile_name = (contacts[0].get("profile") or {}).get("name") if contacts else None

    text = None
    image_url = None
    if msg_type == "text":
        text = (message.get("text") or {}).get("body")
    elif msg_type == "image":
        image = message.get("image") or {}
        image_url = image.get("link")
        text = image.get("caption")

    timestamp = message.get("timestamp")
    when = (
        datetime.fromtimestamp(int(timestamp), tz=UTC)
        if timestamp and str(timestamp).isdigit()
        else datetime.now(UTC)
    )

    return InboundMessage(
        phone=str(message.get("from", "")),
        type=msg_type,
        text=text,
        image_url=image_url,
        wa_message_id=message.get("id") or _new_wamid(),
        profile_name=profile_name,
        timestamp=when,
        raw=raw,
    )
