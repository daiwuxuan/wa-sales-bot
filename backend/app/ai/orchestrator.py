"""多轮 tool-calling 编排循环。

真实模型和 Mock 共用这一个循环 —— 它们只在 provider 那一层分叉。

★ 这个模块**不持有数据库会话**，只接收 ID。
  每次落日志、每次执行工具都开一个独立的短事务。
  原因：循环中间要等模型往返 1~5 秒，如果这期间握着一个
  已经 BEGIN IMMEDIATE 的会话，整个数据库（含所有后台页面）会锁死。

★ 工具执行器也不在事务里等 LLM —— execute_tool 自己开自己的会话。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from app.ai import guard
from app.ai.protocol import AssistantTurn, Message, tool_result_message
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.tool_schemas import TOOL_SCHEMAS
from app.core.config import settings
from app.core.database import session_scope
from app.core.ids import new_turn_id
from app.models import AiTurn, ChatLog, Conversation, ToolCallLog, utcnow
from app.tools.context import ToolContext
from app.tools.registry import execute_tool, numeric_whitelist

logger = logging.getLogger(__name__)


@dataclass
class ToolTrace:
    step: int
    name: str
    args: dict
    ok: bool
    error_code: str | None
    duration_ms: int
    result: dict = field(default_factory=dict)

    def to_step_payload(self) -> dict:
        """给前端的精简形态（不把整个结果塞进去，详情单独取）。"""
        return {
            "step": self.step,
            "tool": self.name,
            "args": self.args,
            "ok": self.ok,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
        }


@dataclass
class TurnResult:
    text: str
    turn_id: str
    status: str  # ok | max_steps | guard_blocked | error
    traces: list[ToolTrace] = field(default_factory=list)
    outbound_images: list[dict] = field(default_factory=list)
    guard_violations: list[str] = field(default_factory=list)
    escalated: bool = False


def _load_history(conversation_id: int, limit: int) -> list[Message]:
    """取最近若干条消息作为上下文。

    只取尾部 + 会话摘要，不取全量 —— 长会话会把 token 成本推爆。
    """
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(ChatLog)
                .where(ChatLog.conversation_id == conversation_id)
                .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
                .limit(limit)
            )
        )

    messages: list[Message] = []
    for row in reversed(rows):
        if row.direction == "in":
            if row.body:
                messages.append({"role": "user", "content": row.body})
        else:
            if row.body:
                messages.append({"role": "assistant", "content": row.body, "tool_calls": []})
    return messages


def _log_tool_call(
    turn_id: str,
    conversation_id: int | None,
    step: int,
    name: str,
    args: dict,
    result: dict,
    duration_ms: int,
) -> None:
    with session_scope() as db:
        db.add(
            ToolCallLog(
                turn_id=turn_id,
                conversation_id=conversation_id,
                step_index=step,
                tool_name=name,
                arguments_json=json.dumps(args, ensure_ascii=False),
                result_json=json.dumps(result, ensure_ascii=False),
                result_ok=bool(result.get("ok")),
                error_code=result.get("error_code"),
                duration_ms=duration_ms,
                created_at=utcnow(),
            )
        )


def _load_state(conversation_id: int) -> dict:
    """读上一轮结束时存下的会话状态。"""
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None or not conv.state_json:
            return {}
        try:
            return json.loads(conv.state_json)
        except json.JSONDecodeError:
            logger.warning("会话状态损坏，已忽略 conversation=%s", conversation_id)
            return {}


# 工具名 -> 会话状态里的键。显式映射，不做字符串推导 ——
# 推导在 create_order 这种没有 get_ 前缀的名字上会得到错键，
# 而且错了不会报错，只会静默地「状态里没有这个字段」。
STATE_KEYS = {
    "search_inventory": "last_search",
    "get_quote": "last_quote",
    "get_product_image": "last_image",
    "create_order": "last_order",
    "get_customer": "last_customer",
}


def _build_state(traces: list[ToolTrace], previous: dict) -> dict:
    """把本轮的工具结果合并进会话状态。

    search_inventory 的结果要截断 —— 一次查询可能带 8 条匹配，
    全存进去会让 state_json 越滚越大，而且下一轮真正有用的
    只有实际选中的那一条。
    """
    state = dict(previous)

    for trace in traces:
        if not trace.ok:
            continue

        key = STATE_KEYS.get(trace.name)
        if key is None:
            continue

        if trace.name == "search_inventory":
            data = trace.result.get("data") or {}
            matches = data.get("matches") or []
            if not matches:
                continue
            state[key] = {
                "ok": True,
                "data": {
                    "matches": matches[:1],
                    # query 要一起存：下一轮靠它判断这次搜索是不是按款号收窄的
                    "query": data.get("query"),
                },
                "available_options": trace.result.get("available_options"),
            }
        else:
            state[key] = trace.result

    return state


def _persist_state(conversation_id: int, state: dict) -> None:
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is not None:
            conv.state_json = json.dumps(state, ensure_ascii=False)


def _escalate(conversation_id: int, reason: str) -> None:
    """转人工。AI 不再自动回复这个会话。"""
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is not None:
            conv.ai_enabled = False
            conv.takeover_at = utcnow()
            conv.takeover_reason = reason
            conv.assigned_to = None


def _finalize_turn(
    turn_id: str,
    *,
    status: str,
    text: str | None,
    violations: list[str],
    allowed: set[str],
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    error: str | None = None,
) -> None:
    with session_scope() as db:
        turn = db.get(AiTurn, turn_id)
        if turn is None:
            return
        turn.status = status
        turn.final_text = text
        turn.guard_violations_json = json.dumps(violations, ensure_ascii=False)
        turn.allowed_numbers_json = json.dumps(sorted(allowed), ensure_ascii=False)
        turn.prompt_tokens = input_tokens
        turn.completion_tokens = output_tokens
        turn.latency_ms = latency_ms
        turn.error = error


def run_turn(
    *,
    provider,
    conversation_id: int,
    customer_id: int,
    customer_phone: str,
    inbound_text: str,
    inbound_chat_log_id: int | None = None,
) -> TurnResult:
    """跑完一次客户消息触发的完整 AI 处理。"""
    started = time.perf_counter()
    turn_id = new_turn_id()
    ctx = ToolContext(
        customer_id=customer_id,
        customer_phone=customer_phone,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )

    with session_scope() as db:
        db.add(
            AiTurn(
                id=turn_id,
                conversation_id=conversation_id,
                inbound_chat_log_id=inbound_chat_log_id,
                llm_mode=provider.mode,
                model=provider.name,
                status="running",
                created_at=utcnow(),
            )
        )

    previous_state = _load_state(conversation_id)
    messages = _load_history(conversation_id, settings.history_limit)
    messages.append({"role": "user", "content": inbound_text})

    traces: list[ToolTrace] = []
    outbound_images: list[dict] = []
    called_signatures: set[tuple[str, str]] = set()

    # ★ 白名单要用上一轮的会话状态打底，不能只装本轮的。
    #
    #   客户说「来3件」时，合法回复会引用**上一轮**那份报价的数字。
    #   只装本轮结果的话，这些数字会被判成「编造」，回复被拦下、
    #   模型重试、再被拦下，直到步数耗尽 —— 客户收到的是
    #   「不好意思，我需要和同事确认一下价格」这种毫无信息量的兜底话术。
    #
    #   这不削弱保证：会话状态里的数字本身就是工具产出的，
    #   只是产生于上一轮而已。保证始终是「模型不能说服务端没给过的数字」。
    allowed: set[str] = numeric_whitelist(previous_state)
    total_in = total_out = 0

    try:
        for step in range(settings.max_agent_steps):
            turn: AssistantTurn = provider.complete(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_SCHEMAS,
                # 上一轮的报价/变体/订单。没有它，客户说「要了」时
                # 模型不知道有报价可用，只会重新查一遍货。
                context=_build_state(traces, previous_state),
            )
            total_in += turn.input_tokens
            total_out += turn.output_tokens

            messages.append(turn.to_message())

            if not turn.has_tool_calls:
                text = turn.content or ""
                verdict = guard.check(text, allowed)

                if verdict.suspicious:
                    # 放行但留痕：这些数字不在白名单里，
                    # 如果事后发现是编造的，日志里有据可查。
                    logger.info(
                        "接地校验：可疑数字 %s | turn=%s", verdict.suspicious, turn_id
                    )

                if verdict.ok:
                    _finalize_turn(
                        turn_id, status="ok", text=text, violations=[], allowed=allowed,
                        input_tokens=total_in, output_tokens=total_out,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                    _persist_state(
                        conversation_id, _build_state(traces, previous_state)
                    )
                    return TurnResult(
                        text=text, turn_id=turn_id, status="ok",
                        traces=traces, outbound_images=outbound_images,
                    )

                # 编造了价格：给模型一次自我纠正的机会，附上更严的提示
                logger.warning("接地校验未通过 %s | turn=%s", verdict.violations, turn_id)
                messages.append(
                    {
                        "role": "system",
                        "content": guard.STRICT_NUDGE.format(
                            numbers="、".join(verdict.violations)
                        ),
                    }
                )
                continue

            for tc in turn.tool_calls:
                signature = (tc.name, tc.arguments_json())

                if signature in called_signatures:
                    # 模型原地打转，重复调用同一个工具。回一条错误让它换路子，
                    # 而不是无限重试烧 token。
                    messages.append(
                        tool_result_message(
                            tc.id, tc.name,
                            {
                                "ok": False,
                                "error_code": "duplicate_call",
                                "message": "这个调用刚才已经做过了，请换一个做法或直接回复客户。",
                            },
                        )
                    )
                    continue
                called_signatures.add(signature)

                result = execute_tool(tc.name, tc.arguments, ctx)
                duration_ms = result.pop("_duration_ms", 0)

                _log_tool_call(
                    turn_id, conversation_id, step, tc.name, tc.arguments, result, duration_ms
                )

                # ★ 白名单只在这里扩充 —— 只有工具结果能增加可信数字
                allowed |= numeric_whitelist(result)

                traces.append(
                    ToolTrace(
                        step=step, name=tc.name, args=tc.arguments,
                        ok=bool(result.get("ok")),
                        error_code=result.get("error_code"),
                        duration_ms=duration_ms, result=result,
                    )
                )

                if tc.name == "get_product_image" and result.get("ok"):
                    data = result.get("data") or {}
                    if data.get("image_url"):
                        outbound_images.append(
                            {
                                "url": data["image_url"],
                                "caption": f"{data.get('product_name', '')} {data.get('color', '')}".strip(),
                            }
                        )

                messages.append(tool_result_message(tc.id, tc.name, result))

        # 步数用完了还没收敛 —— 转人工，别让客户干等
        logger.warning("AI 达到步数上限 %s | turn=%s", settings.max_agent_steps, turn_id)
        _escalate(conversation_id, "max_steps")
        _finalize_turn(
            turn_id, status="max_steps", text=guard.SAFE_FALLBACK,
            violations=[], allowed=allowed, input_tokens=total_in, output_tokens=total_out,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        # 步数用尽也要存状态：本轮查到的货下一轮还有用
        _persist_state(conversation_id, _build_state(traces, previous_state))
        return TurnResult(
            text=guard.SAFE_FALLBACK, turn_id=turn_id, status="max_steps",
            traces=traces, outbound_images=outbound_images, escalated=True,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("AI turn 失败 | turn=%s", turn_id)
        # 模型侧异常（超时、额度、网络）不能让客户收不到任何回复
        _escalate(conversation_id, "ai_error")
        _finalize_turn(
            turn_id, status="error", text=guard.SAFE_FALLBACK, violations=[],
            allowed=allowed, input_tokens=total_in, output_tokens=total_out,
            latency_ms=int((time.perf_counter() - started) * 1000), error=str(exc)[:500],
        )
        _persist_state(conversation_id, _build_state(traces, previous_state))
        return TurnResult(
            text=guard.SAFE_FALLBACK, turn_id=turn_id, status="error",
            traces=traces, outbound_images=outbound_images, escalated=True,
        )
