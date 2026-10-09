"""模型公共部分。

时间一律存 UTC。报表按本地日聚合时在 Python 侧算好区间边界再传 UTC 值查询
（SQLite 没有 AT TIME ZONE），否则「今日 GMV」会错 8 小时。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base as Base  # re-export，让 models 只 import 这一个


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
