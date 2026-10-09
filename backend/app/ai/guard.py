"""输出接地校验 —— 「AI 不能说一个工具没给过的数字」的执行机制。

这是防造假的第三层：
  L0 信息隔离：最低价/成本从来没有进过模型上下文
  L1 枚举白名单：get_quote 给的是封闭列表，模型只能选不能算
  L2 服务端重算：create_order 参数里没有价格，价格从库里按 offer_id 取回
  L3 出口校验：← 这个文件。回复发出前扫出所有数字，逐个比对白名单

白名单**只**由本轮工具结果扩充。模型自己说过的话永远不进白名单 ——
否则它随便说一个数字，下一轮就把自己洗白了。

误报和漏报的代价不一样，所以分两档：
  · 数字带明确价格上下文（¥199 / 199元）且不在白名单 -> 违规，拦截
  · 光秃秃的数字且不在白名单 -> 记为可疑，放行但记日志
前者是编造价格，后者很可能是「3 天发货」这类正常表达。
一律拦截会把正常回复也挡下来，转人工转得莫名其妙。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 明确的价格上下文：货币符号在前，或者单位在后
PRICE_WITH_SYMBOL = re.compile(r"[¥￥$]\s*(\d+(?:[.,]\d+)?)")
PRICE_WITH_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:元|块钱|块|RMB|rmb)")

ANY_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# 这些词跟在数字后面时，说明它在描述时长/数量/折扣，不是价格
NON_PRICE_SUFFIX = re.compile(
    r"\s*(?:小时|分钟|天|周|个月|年|件|个|条|双|套|折|%|％|码|星|分|秒|次|款|色)"
)

# 常见结构数字，永远不该被当成价格
STRUCTURAL = {"0", "1", "2", "3", "4", "5"}


@dataclass
class Verdict:
    ok: bool
    violations: list[str] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return ",".join(self.violations)


def normalize_number(token: str) -> str:
    """归一化成可比较的形式：199.00 -> 199，1,199 -> 1199。"""
    cleaned = token.replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _has_non_price_suffix(text: str, end: int) -> bool:
    return bool(NON_PRICE_SUFFIX.match(text, end))


def check(text: str, allowed: set[str]) -> Verdict:
    """校验一段回复文本。allowed 是本轮所有工具结果里的数字集合。"""
    if not text:
        return Verdict(ok=True)

    violations: list[str] = []
    suspicious: list[str] = []

    def consider(token: str, pos: int, end: int, *, priced: bool) -> None:
        normalized = normalize_number(token)
        if normalized in allowed:
            return
        if _has_non_price_suffix(text, end):
            return  # 是时长/数量，不是价格
        if priced:
            if normalized not in violations:
                violations.append(normalized)
        elif len(normalized) >= 2 and normalized not in STRUCTURAL:
            # 两位数以上、又没有价格上下文 —— 记下来但不拦
            if normalized not in suspicious:
                suspicious.append(normalized)

    for match in PRICE_WITH_SYMBOL.finditer(text):
        consider(match.group(1), match.start(1), match.end(1), priced=True)

    for match in PRICE_WITH_UNIT.finditer(text):
        consider(match.group(1), match.start(1), match.end(1), priced=True)

    for match in ANY_NUMBER.finditer(text):
        # 已经被价格规则处理过的位置跳过，避免重复计数
        if any(m.start() <= match.start() < m.end() for m in
               list(PRICE_WITH_SYMBOL.finditer(text)) + list(PRICE_WITH_UNIT.finditer(text))):
            continue
        consider(match.group(0), match.start(), match.end(), priced=False)

    return Verdict(ok=not violations, violations=violations, suspicious=suspicious)


STRICT_NUDGE = (
    "【系统校验未通过】你刚才的回复里出现了工具没有提供过的价格数字：{numbers}。\n"
    "请重新组织回复，只使用工具返回过的金额。如果客户在议价而你手上没有更低的档位，"
    "就直接说明这是当前能给到的价格，不要编造任何数字。"
)

SAFE_FALLBACK = "不好意思，我需要和同事确认一下价格，稍后回复您。"
