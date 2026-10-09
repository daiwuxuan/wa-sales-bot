"""真实 WhatsApp Cloud API 通道 —— 骨架，本阶段不启用。

留这个文件是为了让「切真实 API」这件事有一个明确的落点：
需要写的东西都在这里，业务代码一行都不用动。

接入时要做的三件事：
  1. 配 WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID / APP_SECRET
  2. 把入口路由从 /api/mock-webhook 换成 /api/webhook/whatsapp
  3. verify_signature 从「跳过」改成真校验（代码已写好，见下）

★ 图片消息有个必须提前知道的坑：
  Cloud API 发图有两种方式 ——
    image.link：必须是**公网 HTTPS 可达**的地址。localhost 不行。
    image.id  ：先 POST /{phone_number_id}/media 上传拿到 media_id，再用 id 发。
  生产环境必须用第二种。所以 send() 里留了 upload_media 的位置，
  并且要用 wa_media_id 做缓存，不能每条消息都重传一次。
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from app.channels.base import InboundMessage, OutboundMessage, parse_cloud_envelope

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """校验 X-Hub-Signature-256。

    真实接入时**必须**开着。没有这个校验，任何人都能伪造客户消息，
    包括伪造「确认下单」把库存和订单搅乱。
    """
    if not signature_header or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


class WhatsAppCloudChannel:
    """真实通道。本阶段不实例化。"""

    def __init__(self, token: str, phone_number_id: str):
        self._token = token
        self._phone_number_id = phone_number_id

    def parse_inbound(self, raw: dict) -> InboundMessage | None:
        # 和 Mock 通道共用同一套信封解析 —— 两种形态早就统一了
        return parse_cloud_envelope(raw)

    def send(self, message: OutboundMessage) -> dict:
        payload = message.render_cloud_payload()

        # 生产用 image.id 而非 image.link（localhost 不可达）
        if message.type == "image" and message.image_url:
            media_id = self._ensure_media(message.image_url)
            if media_id:
                payload["image"] = {"id": media_id}
                if message.caption:
                    payload["image"]["caption"] = message.caption

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self._phone_number_id}/messages"
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()

    def _ensure_media(self, image_url: str) -> str | None:
        """把图片上传到 Meta 换 media_id。二次实现时记得加缓存。"""
        logger.warning("_ensure_media 尚未实现，仍在用 image.link（仅开发可用）")
        return None
