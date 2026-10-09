"""金额处理。全系统唯一的「元 <-> 分」转换入口。

为什么全链路用整数分存储，而不是 Numeric/Decimal 列：
SQLAlchemy 的 Numeric 在 SQLite 上会走浮点（并抛
`SAWarning: Dialect sqlite+pysqlite does *not* support Decimal objects natively`），
0.1 + 0.2 != 0.3 会直接体现在报价总额上。整数分在 SQLite 和 Postgres 上
行为完全一致，切库零风险，也不需要担心四舍五入发生在哪一层。

对外序列化时用 amount_cents + display 两个字段一起给，
前端永远不需要自己做除法。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_ONE = Decimal("1")


def to_cents(amount: Decimal | str | int | float) -> int:
    """元 -> 整数分。"""
    if isinstance(amount, Decimal):
        value = amount
    elif isinstance(amount, int):
        return amount * 100
    else:
        # 关键：float 必须先转字符串再进 Decimal。
        # Decimal(0.1) 会把二进制误差原样带进来，Decimal("0.1") 不会。
        value = Decimal(str(amount))
    return int((value * 100).quantize(_ONE, rounding=ROUND_HALF_UP))


def to_yuan(cents: int) -> Decimal:
    """整数分 -> 元（两位小数）。"""
    return (Decimal(cents) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def amount(cents: int | None) -> dict | None:
    """对外统一的金额表示。

    同时给分和显示串，避免前端各自实现一遍格式化 —— 也避免前端
    把分当成元显示（这是这类系统最常见的线上事故）。
    """
    if cents is None:
        return None
    return {"cents": cents, "display": f"¥{to_yuan(cents):.2f}", "currency": "CNY"}


def apply_rate(cents: int, rate: Decimal) -> int:
    """按折扣率计价，四舍五入到分。

    用整数分参与运算，避免 Decimal 在中间步骤产生多余精度。
    """
    return int((Decimal(cents) * rate).quantize(_ONE, rounding=ROUND_HALF_UP))
