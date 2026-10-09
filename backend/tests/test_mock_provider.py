"""Mock provider 的对话决策。

它是整个离线演示的引擎，而且规则式实现特别容易在改动中静默退化 ——
「客户说买却不下单」这类问题不会报错，只会在演示时表现成对话卡住。

这里只测决策（该调哪个工具、该不该回话），不碰数据库，
所以跑得很快，适合作为回归网。
"""

from __future__ import annotations

import json

from app.ai.providers.mock_provider import MockProvider, derive_slots
from app.core.database import SessionLocal
from app.models import AttributeAlias


def _seed_aliases():
    with SessionLocal() as session:
        for canonical, alias in [
            ("black", "黑色"), ("black", "黑"),
            ("white", "白色"), ("white", "白"),
            ("XL", "xl"), ("L", "l"), ("M", "m"),
        ]:
            kind = "color" if canonical in ("black", "white") else "size"
            session.add(AttributeAlias(attr_type=kind, alias=alias, canonical=canonical))
        session.commit()


def _search_ok(
    color="black", size="XL", variant_id=3, in_stock=True, qty=5, sku="TSHIRT-001",
    query_sku=None,
) -> dict:
    """构造一个 search_inventory 的成功结果。

    query_sku 对应工具返回的 data.query.sku —— 记录这次搜索是否按款号收窄过。
    下一轮要不要沿用款号全靠它，所以测试里必须显式给。
    """
    return {
        "ok": True,
        "data": {
            "matches": [
                {
                    "variant_id": variant_id,
                    "sku": sku,
                    "product_name": "圆领T恤",
                    "color": color,
                    "color_label": "黑色" if color == "black" else color,
                    "size": size,
                    "size_label": size,
                    "in_stock": in_stock,
                    "available_qty": qty,
                }
            ],
            "query": {"sku": query_sku, "color": color, "size": size},
        },
    }


def _quote_ok(quote_id="Q001", variant_id=3, qty=1, unit="¥209.00") -> dict:
    return {
        "ok": True,
        "data": {
            "quote_id": quote_id,
            "total_display": unit,
            "authorized_offers": [
                {
                    "offer_id": f"O0{variant_id}",
                    "variant_id": variant_id,
                    "qty": qty,
                    "unit_price_cents": 20900,
                    "unit_price_display": unit,
                    "line_total_cents": 20900 * qty,
                    "line_total_display": unit,
                    "discount_pct": 0,
                }
            ],
        },
    }


def _tool_msg(name: str, payload: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": f"c-{name}",
        "name": name,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _provider() -> MockProvider:
    return MockProvider(session_factory=SessionLocal)


def _next(provider: MockProvider, messages: list[dict], context: dict | None = None):
    turn = provider.complete(system="", messages=messages, tools=[], context=context)
    return turn


def _tool_name(turn) -> str | None:
    return turn.tool_calls[0].name if turn.tool_calls else None


class TestFirstTurn:
    def test_extracts_color_and_size_from_chinese(self):
        _seed_aliases()
        turn = _next(_provider(), [{"role": "user", "content": "有黑色XL吗"}])
        assert _tool_name(turn) == "search_inventory"
        args = turn.tool_calls[0].arguments
        assert args["color"] == "black"
        assert args["size"] == "XL"

    def test_pipeline_order_is_search_then_image_then_quote(self):
        _seed_aliases()
        provider = _provider()
        msgs = [{"role": "user", "content": "有黑色XL吗"}]

        turn = _next(provider, msgs)
        assert _tool_name(turn) == "search_inventory"
        msgs += [turn.to_message(), _tool_msg("search_inventory", _search_ok())]

        turn = _next(provider, msgs)
        assert _tool_name(turn) == "get_product_image"
        msgs += [turn.to_message(), _tool_msg("get_product_image", {"ok": True, "data": {"image_url": "http://x/y.png"}})]

        turn = _next(provider, msgs)
        assert _tool_name(turn) == "get_quote"
        msgs += [turn.to_message(), _tool_msg("get_quote", _quote_ok())]

        turn = _next(provider, msgs)
        assert turn.tool_calls == [], "报价之后应该直接回话，不该继续调工具"
        assert "¥209.00" in turn.content


class TestConfirmToOrder:
    def test_plain_confirmation_creates_order(self):
        _seed_aliases()
        context = {"last_quote": _quote_ok(), "last_search": _search_ok()}
        turn = _next(_provider(), [{"role": "user", "content": "要了"}], context)
        assert _tool_name(turn) == "create_order"
        assert turn.tool_calls[0].arguments["quote_id"] == "Q001"

    def test_quantity_confirmation_triggers_requote_first(self):
        """「来3件」不能直接按 1 件的报价下单 —— 数量档位是另一份价格授权。"""
        _seed_aliases()
        context = {"last_quote": _quote_ok(qty=1), "last_search": _search_ok()}
        turn = _next(_provider(), [{"role": "user", "content": "来3件"}], context)

        assert _tool_name(turn) == "get_quote", "数量变了必须先重新报价"
        assert turn.tool_calls[0].arguments["qty"] == 3

    def test_after_requote_the_order_goes_through(self):
        """重新报价之后，同一轮循环里就该能下单了。"""
        _seed_aliases()
        context = {"last_quote": _quote_ok(qty=1), "last_search": _search_ok()}
        provider = _provider()
        msgs = [{"role": "user", "content": "来3件"}]

        turn = _next(provider, msgs, context)
        assert _tool_name(turn) == "get_quote"
        msgs += [turn.to_message(), _tool_msg("get_quote", _quote_ok(qty=3))]

        turn = _next(provider, msgs, context)
        assert _tool_name(turn) == "create_order"
        assert turn.tool_calls[0].arguments["items"][0]["qty"] == 3

    def test_confirmation_without_context_does_not_fabricate_an_order(self):
        """没有报价上下文时不能凭空下单。"""
        _seed_aliases()
        turn = _next(_provider(), [{"role": "user", "content": "要了"}], None)
        assert _tool_name(turn) != "create_order"


class TestOutOfStock:
    def test_sold_out_gets_a_specific_message(self):
        """卖完了要说「卖完了」，不能说「暂无可用报价」。"""
        _seed_aliases()
        provider = _provider()
        msgs = [{"role": "user", "content": "有黑色XL吗"}]

        turn = _next(provider, msgs)
        msgs += [
            turn.to_message(),
            _tool_msg("search_inventory", _search_ok(in_stock=False, qty=0)),
        ]

        turn = _next(provider, msgs)
        assert turn.tool_calls == [], "卖完了不该继续往下调报价"
        assert "卖完" in (turn.content or "")
        assert "暂无可用报价" not in (turn.content or "")

    def test_not_found_lists_alternatives(self):
        _seed_aliases()
        provider = _provider()
        msgs = [{"role": "user", "content": "有蓝色XXL吗"}]

        turn = _next(provider, msgs)
        msgs += [
            turn.to_message(),
            _tool_msg(
                "search_inventory",
                {
                    "ok": False,
                    "error_code": "variant_not_found",
                    "available_options": {"colors": ["black", "white"], "sizes": ["M", "L"]},
                },
            ),
        ]

        turn = _next(provider, msgs)
        assert turn.tool_calls == []
        assert "black" in (turn.content or "")


class TestMindChange:
    def test_new_attributes_trigger_a_fresh_search(self):
        """客户改口：先说黑色 XL，又说要白色 L —— 必须重新查，不能沿用旧结果。"""
        _seed_aliases()
        provider = _provider()

        msgs = [{"role": "user", "content": "有黑色XL吗"}]
        turn = _next(provider, msgs)
        assert turn.tool_calls[0].arguments["color"] == "black"
        msgs += [turn.to_message(), _tool_msg("search_inventory", _search_ok())]

        # 客户改口，开启新一轮
        msgs.append({"role": "user", "content": "算了要白色L"})
        turn = _next(provider, msgs, {"last_search": _search_ok()})

        assert _tool_name(turn) == "search_inventory", "改口后必须重新查货"
        args = turn.tool_calls[0].arguments
        assert args["color"] == "white"
        assert args["size"] == "L"


class TestSkuScope:
    """款号收窄的继承规则。

    这一块曾经出过一个严重缺陷：客户问某款（售罄）后说「要了」，
    重新搜索时款号丢失，搜索范围从「这一款」扩大到「全库」，
    结果搜到一件客户从没提过的商品并准备卖给他。
    属于「静默卖错货」，不测就会再犯。
    """

    def test_sold_out_sku_scoped_search_does_not_re_search(self):
        """上一轮按款号查了、结果是缺货，客户说「要了」—— 不该再搜一遍。"""
        _seed_aliases()
        context = {
            "last_search": _search_ok(in_stock=False, qty=0, query_sku="TSHIRT-001"),
        }
        turn = _next(_provider(), [{"role": "user", "content": "要了"}], context)

        assert turn.tool_calls == [], "售罄后客户说「要了」，不该再搜一遍"
        assert "卖完" in (turn.content or "")

    def test_sku_scoped_search_is_kept_on_a_new_question(self):
        """同样是款号收窄过的上一轮，客户换了个问法，款号必须保留。"""
        _seed_aliases()
        context = {"last_search": _search_ok(query_sku="TSHIRT-001")}
        # 这句里没有款号，但有新的颜色尺码
        turn = _next(_provider(), [{"role": "user", "content": "有白色L吗"}], context)

        assert _tool_name(turn) == "search_inventory"
        args = turn.tool_calls[0].arguments
        assert args["sku"] == "TSHIRT-001", "上一轮按款号收窄过，这一轮必须沿用"
        assert args["color"] == "white"
        assert args["size"] == "L"

    def test_sku_is_not_inherited_when_previous_search_was_broad(self):
        """★ 反向保护：上一轮只是宽泛地按颜色尺码搜的，不能把客户锁死到某一款。"""
        _seed_aliases()
        context = {"last_search": _search_ok(query_sku=None, sku="HOODIE-002")}
        turn = _next(_provider(), [{"role": "user", "content": "有白色L吗"}], context)

        args = turn.tool_calls[0].arguments
        assert args["sku"] is None, (
            "上一轮没有指定款号，这一轮不能凭空收窄到当时碰巧命中的那一款 —— "
            "否则客户永远只能在那一款里找货"
        )


class TestConfirmWithNothingToBuy:
    def test_sold_out_confirmation_does_not_pivot_to_another_product(self):
        """★ 客户要买的东西卖完了，不能顺手换一件他没提过的卖给他。"""
        _seed_aliases()
        context = {
            "last_search": _search_ok(in_stock=False, qty=0, query_sku="TSHIRT-001"),
        }
        turn = _next(_provider(), [{"role": "user", "content": "要了"}], context)

        assert turn.tool_calls == [], "卖完了就不该再调任何工具去『找货』"
        assert "卖完" in (turn.content or "")
        # 绝不能出现「换一件给您」这种自作主张
        assert "variant_id" not in (turn.content or "")


class TestSlots:
    def test_current_turn_isolated_from_earlier_turns(self):
        """「当前轮」只包含最后一条用户消息之后的工具结果。"""
        msgs = [
            {"role": "user", "content": "有黑色XL吗"},
            _tool_msg("search_inventory", _search_ok()),
            {"role": "user", "content": "要了"},
        ]
        slots = derive_slots(msgs)
        assert slots.current_turn_results == []
        # 但跨轮的 last_search 仍然保留，供确认下单时使用
        assert slots.last_search is not None

    def test_context_is_loaded_into_slots(self):
        slots = derive_slots([{"role": "user", "content": "要了"}], {"last_quote": _quote_ok()})
        assert slots.last_quote is not None
        assert slots.current_turn_results == []
