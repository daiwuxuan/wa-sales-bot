"""真实 OpenAI 客户端（Responses API）。

用 Responses 而不是 Chat Completions：2026 年新项目做 function calling
官方推荐前者，而且新模型上 Chat Completions 对工具调用已有限制。
额外好处是默认 strict 模式、且**直接返回已解析的参数对象** ——
Chat Completions 返回的是 JSON 字符串，约 1~2% 概率需要重试解析。

想换回 Chat Completions 的话，只需要改这个文件里的两处翻译，
编排循环和工具层一行都不用动。
"""

from __future__ import annotations

import json
import logging

from openai import APIError, APITimeoutError, OpenAI

from app.ai.protocol import AssistantTurn, Message, ToolCall, render_context_note

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20.0


class OpenAIProvider:
    mode = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self._client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1)
        self._model = model
        self.name = model

    # ── 中立消息 -> Responses API input items ──────────────
    def _to_input_items(self, messages: list[Message]) -> list[dict]:
        items: list[dict] = []
        for msg in messages:
            role = msg.get("role")

            if role in ("user", "system"):
                items.append({"role": role, "content": msg.get("content") or ""})

            elif role == "assistant":
                if msg.get("content"):
                    items.append({"role": "assistant", "content": msg["content"]})
                for tc in msg.get("tool_calls") or []:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc["id"],
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        }
                    )

            elif role == "tool":
                # Responses API 用 function_call_output 配对，靠 call_id 关联
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg["tool_call_id"],
                        "output": msg.get("content") or "",
                    }
                )
        return items

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.2,
        context: dict | None = None,
    ) -> AssistantTurn:
        instructions = system
        note = render_context_note(context)
        if note:
            instructions = f"{system}\n\n{note}"

        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=self._to_input_items(messages),
                tools=tools,
                temperature=temperature,
                store=False,  # 不留存对话，避免把客户手机号写进 OpenAI 侧
            )
        except APITimeoutError:
            logger.warning("OpenAI 请求超时")
            raise
        except APIError as exc:
            logger.warning("OpenAI 请求失败: %s", exc)
            raise

        return self._parse(response)

    @staticmethod
    def _parse(response) -> AssistantTurn:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        # 注意：输出项不一定以 message 开头（可能先有 reasoning item），
        # 所以不能取 output[0] 当答案，必须按 type 过滤。
        for item in response.output or []:
            item_type = getattr(item, "type", None)

            if item_type == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    logger.warning("模型返回了无法解析的参数: %s", item.arguments)
                    args = {}
                tool_calls.append(ToolCall(id=item.call_id, name=item.name, arguments=args))

            elif item_type == "message":
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) == "output_text":
                        text_parts.append(part.text)

        usage = getattr(response, "usage", None)
        return AssistantTurn(
            content="".join(text_parts).strip() or None,
            tool_calls=tool_calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


def probe(api_key: str, model: str) -> tuple[bool, str]:
    """启动探活。1 个 token 的请求，确认 key 和模型真的可用。

    演示到一半才发现 key 过期或没额度，是最糟糕的失败方式。
    """
    try:
        client = OpenAI(api_key=api_key, timeout=10.0, max_retries=0)
        client.responses.create(model=model, input="hi", max_output_tokens=16, store=False)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
