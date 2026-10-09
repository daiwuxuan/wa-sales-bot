"""模拟接收 WhatsApp 消息。

★ 路由写成同步 def 而不是 async def。
  数据库层是全同步的，在 async def 里调同步 ORM 会阻塞事件循环 ——
  AI 处理的那几秒里，所有请求（包括商品图片）全部无响应。
  同步路由会被 FastAPI 自动丢进线程池，这是本项目唯一正确的组合。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.channels import mock_channel
from app.services import conversation as conversation_service

router = APIRouter(prefix="/api", tags=["mock-webhook"])


class FlatInbound(BaseModel):
    """开发用的扁平信封。

    真实 Cloud API 的原生信封也接受，走同一个 parse_inbound。
    """

    phone: str = Field(..., description="客户手机号，E.164 不带 +，如 8613800138000")
    type: str = Field(default="text", description="text | image")
    text: str | None = None
    image_url: str | None = None
    message_id: str | None = Field(
        default=None,
        description="消息 ID。同一个 ID 重复提交会被识别为重投并忽略。留空则自动生成。",
    )
    profile_name: str | None = None


@router.post("/mock-webhook")
def mock_webhook(payload: dict = Body(...)) -> dict:
    # 注意 Body(...) 不能省。裸的 `payload: dict` 会被 FastAPI 当成
    # query 参数（dict 是标量容器类型，不隐含 request body），
    # 结果是请求体根本没被解析，直接 400。
    """接收一条模拟客户消息，跑完 AI 处理后返回完整轨迹。

    同步返回（而不是先 202 再轮询）是为了演示方便 ——
    curl 一下就看到了全部 tool 调用。
    真实通道必须立刻返回 2xx 再异步处理，因为 Cloud API 5 秒拿不到
    2xx 就会重投，会引发重投风暴。那个逻辑在 whatsapp_cloud 接入时补。
    """
    inbound = mock_channel.parse_inbound(payload)
    if inbound is None:
        raise HTTPException(status_code=400, detail="无法解析的消息体")

    if not inbound.phone:
        raise HTTPException(status_code=400, detail="缺少 phone")

    return conversation_service.handle_inbound(inbound)


@router.get("/mock-webhook/outbox")
def outbox(since: int = 0) -> dict:
    """取出站消息。

    ★ 每一条 payload 都逐字是 Cloud API /messages 的请求体形态 ——
      将来接真实 API 时，这些 JSON 原样就是发给 Meta 的东西。
    """
    items = mock_channel.drain_outbox(since)
    return {"count": len(items), "messages": items}
