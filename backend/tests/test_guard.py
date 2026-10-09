"""接地校验。

它要同时防住两个方向的错误，而这两个方向的代价都很高：
  · 漏报 —— 放走了模型编造的金额，客户拿到一个不存在的价格
  · 误报 —— 把正常回复拦下来，客户收到「稍后回复您」这种无信息量的兜底话术，
           而且会一直重试到步数耗尽

误报这一侧特别隐蔽：日志里只看得到 max_steps，看不出是被校验拦下的。
"""

from __future__ import annotations

import json

from app.ai import guard
from app.tools.registry import numeric_whitelist


class TestViolations:
    def test_invented_price_with_currency_symbol_is_caught(self):
        verdict = guard.check("这款给您 ¥188.00 就行", allowed={"199", "19900"})
        assert not verdict.ok
        assert "188" in verdict.violations

    def test_invented_price_with_unit_is_caught(self):
        verdict = guard.check("188 元给您", allowed={"199"})
        assert not verdict.ok
        assert "188" in verdict.violations

    def test_whitelisted_price_passes(self):
        verdict = guard.check("单价 ¥199.00", allowed={"199", "19900"})
        assert verdict.ok

    def test_normalized_forms_match(self):
        """199 / 199.00 / 1,199 要能互相匹配，否则会大面积误报。"""
        assert guard.check("¥199", allowed={"199"}).ok
        assert guard.check("¥199.00", allowed={"199"}).ok
        assert guard.check("¥1,199", allowed={"1199"}).ok

    def test_non_price_numbers_are_not_flagged(self):
        """「3 天发货」「24 小时」不是价格，不能拦。"""
        verdict = guard.check("大概 3 天发货，24 小时内回复", allowed=set())
        assert verdict.ok


class TestNoFalsePositives:
    def test_quantity_and_time_words_pass_even_without_whitelist(self):
        for text in ["可以买 5 件", "48 小时发货", "7 天无理由", "打 8 折"]:
            assert guard.check(text, allowed=set()).ok, f"误报: {text}"

    def test_bare_number_is_suspicious_but_not_blocked(self):
        """没有价格上下文的裸数字只记录、不拦截 —— 拦了会挡住正常对话。"""
        verdict = guard.check("我们有 88 种颜色", allowed=set())
        assert verdict.ok
        assert "88" in verdict.suspicious

    def test_previous_turn_quote_is_quotable(self):
        """★ 跨轮引用上一轮的报价必须放行。

        客户说「来3件」时，合法回复会引用上一轮那份报价的数字。
        如果白名单只装本轮的工具结果，这些数字会被判成编造 ——
        回复被拦、模型重试、再被拦，直到步数耗尽，
        客户收到的是「稍后回复您」。这个缺陷很难从日志里看出来，
        因为表面上只是 max_steps。
        """
        previous_state = {
            "last_quote": {
                "ok": True,
                "data": {
                    "quote_id": "Q20261008-0001",
                    "total_display": "¥209.00",
                    "authorized_offers": [
                        {
                            "offer_id": "O03",
                            "unit_price_display": "¥209.00",
                            "line_total_display": "¥209.00",
                        }
                    ],
                },
            }
        }
        allowed = numeric_whitelist(previous_state)
        assert "209" in allowed

        # 编排器用上一轮状态给白名单打底，所以这句应当放行
        assert guard.check("刚才说的 ¥209.00 还能用", allowed=allowed).ok

    def test_state_numbers_are_the_only_source(self):
        """白名单里的数字必须全部来自服务端产出的结构。"""
        # 真实工具返回值的形态：分和显示串成对出现，
        # 所以 "20900" 和 "209" 两种写法都在白名单里。
        payload = {
            "ok": True,
            "data": {
                "total_cents": 20900,
                "total_display": "¥209.00",
                "note": "优惠 5% 后",
            },
            "available_options": {"colors": ["black"], "sizes": ["M", "XL"]},
            "_duration_ms": 12,  # 下划线开头的内部字段不该进白名单
        }
        allowed = numeric_whitelist(payload)
        assert "20900" in allowed
        assert "209" in allowed, "显示串里的 209.00 也要能被引用"
        assert "5" in allowed
        assert "12" not in allowed, "内部计时字段不该被当成可引用数字"

    def test_no_derived_values(self):
        """★ 不派生。

        从 20900 推算出 209 看起来方便，但那等于让白名单开始「计算」——
        今天能除以 100，明天就有人加个「顺便算个税」。
        分和元成对出现在真实返回值里，覆盖场景已经够了，
        白名单只做字面量收集，不做任何算术。
        """
        allowed = numeric_whitelist({"total_cents": 20900})
        assert allowed == {"20900"}
        assert "209" not in allowed


class TestWhitelistShape:
    def test_whitelist_survives_json_round_trip(self):
        """状态存进数据库再读出来，数字集合不能变。"""
        state = {"last_quote": {"ok": True, "data": {"total_cents": 61446}}}
        restored = json.loads(json.dumps(state, ensure_ascii=False))
        assert numeric_whitelist(restored) == numeric_whitelist(state)

    def test_booleans_are_not_numbers(self):
        """True 在 Python 里是 int 的子类，不能把订单的 ok 标记当成数字 1。"""
        allowed = numeric_whitelist({"ok": True, "available": False})
        assert allowed == set()
