"""会话、消息、AI 轨迹。

conversations 是「人工接管」状态的唯一权威（ai_enabled 字段）。
放在 customer 上也可以，但接管天然是会话级概念 —— 同一个客户过一个月
再来的是一次新会话，不应该继承上次的接管状态。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")

    # 一个客户一条会话（本项目简化处理，不做会话超时切分）
    external_thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # ★ 人工接管开关。AI 调用前必须先看这个字段。
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    takeover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    takeover_reason: Mapped[str | None] = mapped_column(String(120))

    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 客户最后一条消息 + 24h。窗口外真实 WhatsApp 只允许发预审批模板。
    session_window_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 长会话滚动摘要，控 token 用
    context_summary: Mapped[str | None] = mapped_column(Text)

    # ★ 跨轮次的会话状态：上一轮的报价、查到的变体、发过的图、下过的单。
    #
    #   为什么必须持久化：chat_logs 只存最终文本，工具结果在回合结束就没了。
    #   没有这个字段，下一轮模型根本不知道「上一轮已经报过价」，
    #   客户说「要了」时它只能重新查一遍货 —— 而且很可能查到别的变体。
    #   真实模型和 Mock 都读这个字段。
    state_json: Mapped[str | None] = mapped_column(Text)

    unread_count: Mapped[int] = mapped_column(Integer, default=0)

    customer: Mapped["Customer"] = relationship(lazy="selectin")  # noqa: F821


class ChatLog(Base):
    """全量消息。入站和出站都在这里。"""

    __tablename__ = "chat_logs"
    __table_args__ = (
        Index("ix_chat_logs_conv_created", "conversation_id", "created_at"),
        Index("ix_chat_logs_phone_created", "phone", "created_at"),
        # 部分唯一索引：只约束非空值，出站消息（没有 wa_message_id）不受影响。
        # SQLite 和 Postgres 都支持。
        Index(
            "uq_chat_logs_wa_message_id",
            "wa_message_id",
            unique=True,
            sqlite_where=text("wa_message_id IS NOT NULL"),
            postgresql_where=text("wa_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))

    # 'in' | 'out'
    direction: Mapped[str] = mapped_column(String(4), index=True)
    # 'text' | 'image' | 'template' | 'interactive'
    message_type: Mapped[str] = mapped_column(String(16), default="text")

    phone: Mapped[str] = mapped_column(String(32), index=True)
    body: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(String(500))

    # ★ WhatsApp 会在你超时或返回非 2xx 时重投同一条消息。
    #   唯一索引（部分索引：只约束非空值）是拦截重投的第一道关。
    wa_message_id: Mapped[str | None] = mapped_column(String(120), index=True)

    # 'ai' | 'human' | 'system'
    sent_by: Mapped[str] = mapped_column(String(16), default="system")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=utcnow
    )


class AiTurn(Base):
    """一次客户消息触发的完整 AI 处理过程。

    把多轮 tool call 串成一个可回放的单元，后台「AI 思考过程」直接渲染这个。
    """

    __tablename__ = "ai_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    inbound_chat_log_id: Mapped[int | None] = mapped_column(ForeignKey("chat_logs.id"))

    # 'openai' | 'mock'
    llm_mode: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))

    # 'ok' | 'max_steps' | 'guard_blocked' | 'error'
    status: Mapped[str] = mapped_column(String(20), default="ok", index=True)

    final_text: Mapped[str | None] = mapped_column(Text)
    guard_violations_json: Mapped[str | None] = mapped_column(Text)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    # 本轮所有工具结果的数字白名单，供接地校验和事后审计
    allowed_numbers_json: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ToolCallLog(Base):
    """工具调用全量落库。这是「AI 做了什么」的唯一事实来源。"""

    __tablename__ = "tool_call_logs"
    __table_args__ = (Index("ix_tool_logs_turn_step", "turn_id", "step_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))

    step_index: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    arguments_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    result_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(48))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
