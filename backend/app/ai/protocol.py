"""LLM 客户端协议。

内部消息格式是**中立的**（形如 Chat Completions），两个 provider 各自翻译：
  · OpenAIProvider 把它翻成 Responses API 的 input items
  · MockProvider 直接读它来推导当前走到哪一步

编排循环只认这个格式，所以「换成真实模型」不需要动循环一行代码 ——
这正是双模式能无缝切换的全部含义。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def arguments_json(self) -> str:
        return json.dumps(self.arguments, ensure_ascii=False, sort_keys=True)


@dataclass
class AssistantTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def to_message(self) -> dict:
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in self.tool_calls
            ],
        }


# 中立消息格式
#   {"role": "system"|"user", "content": str}
#   {"role": "assistant", "content": str|None, "tool_calls": [...]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Message = dict[str, Any]


def tool_result_message(call_id: str, name: str, payload: dict) -> Message:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        # 中文不转义，省 token 也方便排查日志
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


class LLMClient(Protocol):
    name: str
    mode: str  # 'openai' | 'mock'

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.2,
        context: dict | None = None,
    ) -> AssistantTurn: ...


def render_context_note(context: dict | None) -> str | None:
    """把会话状态渲染成给模型看的一段说明。

    真实模型需要知道「上一轮已经报过价」才能响应「要了」去下单 ——
    否则它会重新查一遍货，而且很可能查到不一样的变体。

    刻意渲染成自然语言而不是塞原始 JSON：模型对散文式描述的
    利用率明显更高，也少一堆花括号噪声。

    context 里存的是**工具结果的原始信封**（截断过的），
    所以两个 provider 对同一份数据有完全一致的理解。
    """
    if not context:
        return None

    lines: list[str] = []

    quote = (context.get("last_quote") or {}).get("data") or {}
    if quote:
        offers = "、".join(
            f"{o.get('offer_id')}（{o.get('qty')} 件 {o.get('unit_price_display')}）"
            for o in quote.get("authorized_offers", [])
        )
        lines.append(
            f"上一轮已经给客户报过价：报价单号 {quote.get('quote_id')}，"
            f"合计 {quote.get('total_display')}，可选档位 {offers}。"
            "客户确认购买时，直接用这个 quote_id 和对应的 offer_id 调用 create_order。"
        )

    match = _first_match(context.get("last_search"))
    if match:
        lines.append(
            f"上一轮查到的变体是 variant_id={match.get('variant_id')}"
            f"（{match.get('product_name')} {match.get('color_label')} {match.get('size_label')}，"
            f"可售 {match.get('available_qty')} 件）。"
            "客户没有提到新的颜色或尺码时，沿用这一个，不要再查一次。"
        )

    image = (context.get("last_image") or {}).get("data") or {}
    if image.get("image_url"):
        lines.append("该商品的图片上一轮已经发给客户了，不要重复发送。")

    order = (context.get("last_order") or {}).get("data") or {}
    if order:
        lines.append(
            f"这个会话已经下过单：{order.get('order_no')}，合计 {order.get('total_display')}。"
            "不要重复下单。"
        )

    if not lines:
        return None

    return "【当前会话状态】\n" + "\n".join(f"- {line}" for line in lines)


def _first_match(search_payload: dict | None) -> dict | None:
    matches = ((search_payload or {}).get("data") or {}).get("matches") or []
    return matches[0] if matches else None
