"""工具执行上下文。

★ 这里只放 ID，不放 ORM 对象。
  因为每个工具调用都会开自己的独立会话和短事务 —— 绝不跨 LLM 调用
  持有数据库锁。一次模型往返 1~5 秒，期间持有 BEGIN IMMEDIATE
  会让整个后台（包括所有页面）卡死。

★ customer_phone 是「谁在跟我说话」的唯一权威。
  模型传进来的 phone / customer / customer_tier 一律被忽略，
  只认这个字段。这是防提权的结构性保证 —— 不是靠 prompt 约束模型，
  而是让模型传的值根本没有被读取的地方。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    customer_id: int
    customer_phone: str
    conversation_id: int | None
    turn_id: str
