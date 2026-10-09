"""业务单号与 ID 生成。

单号形如 SO20261008-0007 / Q20261008-0001，方便人工在后台和聊天记录里对照。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _next_seq(db: Session, model, column, prefix: str) -> str:
    """取当天最大序号 +1。

    调用方必须在已经持有写锁的事务里调用（下单路径天然满足），
    否则并发下可能算出两个相同序号 —— 此时唯一索引会拦下来，
    幂等逻辑会把它当成重复请求处理，不会产生脏数据。
    """
    today = _today()
    like = f"{prefix}{today}-%"
    current = db.scalar(select(func.max(column)).where(column.like(like)))
    seq = int(current.split("-")[-1]) + 1 if current else 1
    return f"{prefix}{today}-{seq:04d}"


def next_order_no(db: Session) -> str:
    from app.models.order import Order

    return _next_seq(db, Order, Order.order_no, "SO")


def next_quote_no(db: Session) -> str:
    from app.models.quote import Quote

    return _next_seq(db, Quote, Quote.quote_no, "Q")


def new_turn_id() -> str:
    return uuid.uuid4().hex


def new_storage_key(filename: str) -> str:
    return uuid.uuid4().hex
