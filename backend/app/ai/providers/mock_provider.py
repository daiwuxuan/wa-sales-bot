"""规则式 Mock LLM。

它不是「假数据层」，而是**模型替身**：同样读消息历史、同样发 tool_calls、
返回结构跟真实 provider 完全一致。所以编排循环分不清两者，
没配 API key 也能把整条链路跑完。

设计要点：
1. derive_slots() 是**纯函数** —— 从消息列表里的工具结果反推当前走到哪一步，
   没有隐藏状态。所以它可以被单测、可以重放，不会出现「Mock 自己记错了」。
2. 模板里所有数字都用 slots 里的字段插值，Mock 自己不做任何算术。
   这让它天然满足接地要求，也顺便成为真实模型的行为参照。
3. 「客户改口」是这类规则式实现最容易翻车的地方：
   先问黑色 XL 又说「算了要白色 L」，必须能重新走一遍查询，
   而不是抱着上一轮的报价不放。这里靠「工具结果是否出现在最后一条
   用户消息之后」来判定属于哪一轮，天然支持改口。

真实模型上线前，Mock 是唯一的端到端验证手段，所以这里值得写扎实。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.ai.protocol import AssistantTurn, Message, ToolCall

# 确认购买的意图。故意写得宽 —— 中文客户的确认方式非常多样。
#
# 「来3件」「要2个」这类带数量的确认必须单独覆盖：它既表达了购买意图，
# 又改变了数量，而不同数量对应的是不同的授权价格。
# 漏掉它的话，客户说「来3件」会被当成一次新的咨询，重新走一遍报价，
# 然后永远停在报价那一步 —— 演示时表现为「客户明明说要买，却不下单」。
CONFIRM_PATTERNS = [
    r"[要来买]\s*\d+\s*[个件条双套]",   # 来3件 / 要2个 / 买5条
    r"要了", r"要的", r"下单", r"买了", r"买吧", r"来一?[个件]", r"成交",
    r"可以", r"好的?", r"行", r"要", r"ok", r"OK", r"确认", r"拍下", r"付了",
]
CONFIRM_RE = re.compile("|".join(CONFIRM_PATTERNS), re.IGNORECASE)

# 「算了」「换一个」这类改口信号
CHANGE_RE = re.compile(r"算了|换成?|改[成要]|不要了|换个|另外|其他|别的")

QTY_RE = re.compile(r"(\d+)\s*(?:件|个|条|双|套|pcs?)", re.IGNORECASE)

GREETING_RE = re.compile(r"^(你好|您好|hi|hello|在吗|在么|嗨)[!！。~\s]*$", re.IGNORECASE)


@dataclass
class Slots:
    """从消息历史反推出来的对话状态。"""

    last_user: str = ""
    current_turn_results: list[tuple[str, dict]] = field(default_factory=list)
    last_quote: dict | None = None
    last_search: dict | None = None
    last_image: dict | None = None
    last_order: dict | None = None

    def result_of(self, name: str) -> dict | None:
        for tool_name, payload in reversed(self.current_turn_results):
            if tool_name == name:
                return payload
        return None

    @property
    def did(self) -> set[str]:
        return {name for name, _ in self.current_turn_results}


def _parse_tool_payload(message: Message) -> dict:
    try:
        return json.loads(message.get("content") or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error_code": "unparsable"}


def derive_slots(messages: list[Message], context: dict | None = None) -> Slots:
    """纯函数：从消息历史和会话状态推导当前该做什么。

    「当前轮」= 最后一条用户消息之后发生的工具调用。
    这个定义让改口天然成立：客户发新消息 -> 当前轮清空 -> 重新查询。

    context 是上一轮结束后持久化的状态（上一轮的报价、查到的变体等）。
    ★ 没有它，Mock 就完全没有跨轮记忆：chat_logs 只存最终文本，
      工具结果在回合结束就丢了。客户说「要了」时 Mock 会以为从来没报过价，
      于是重新查一遍货 —— 而且很可能查到别的变体。
    """
    slots = Slots()

    # 先用上一轮的状态打底，本轮的工具结果会覆盖它
    if context:
        slots.last_quote = context.get("last_quote")
        slots.last_search = context.get("last_search")
        slots.last_image = context.get("last_image")
        slots.last_order = context.get("last_order")

    last_user_index = -1
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            last_user_index = i
            slots.last_user = msg.get("content") or ""

    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        name = msg.get("name") or ""
        payload = _parse_tool_payload(msg)

        # 跨轮保留：报价和订单在后续轮次里仍然有效
        if name == "get_quote" and payload.get("ok"):
            slots.last_quote = payload
        elif name == "search_inventory" and payload.get("ok"):
            slots.last_search = payload
        elif name == "get_product_image" and payload.get("ok"):
            slots.last_image = payload
        elif name == "create_order" and payload.get("ok"):
            slots.last_order = payload

        if i > last_user_index:
            slots.current_turn_results.append((name, payload))

    return slots


class MockProvider:
    mode = "mock"
    name = "mock-rules-v1"

    def __init__(self, session_factory: Callable[[], Any] | None = None):
        # 需要查别名表把「黑色」翻成 black —— 这是真实模型天生会做的事，
        # Mock 得自己查。
        self._session_factory = session_factory

    # ── 主入口 ────────────────────────────────────────────
    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.2,
        context: dict | None = None,
    ) -> AssistantTurn:
        slots = derive_slots(messages, context)
        text = slots.last_user

        # 已经下过单了，本轮不再重复动作
        if slots.last_order and "create_order" in slots.did:
            return self._say(self._order_text(slots.last_order))

        # 客户确认购买。依赖 context 里的 last_quote ——
        # 没有它就只能重新查货，然后停在报价那一步永远不下单。
        if self._is_confirming(text) and "create_order" not in slots.did:
            offer = self._best_offer(slots.last_quote)
            requested_qty = self._extract_qty(text)

            if offer is not None:
                if requested_qty == offer.get("qty"):
                    return self._call_create_order(slots)

                # 数量变了（「来3件」）。不能直接按旧报价下单 ——
                # 下单时会校验数量与报价一致，必然被拒。
                # 而且数量档位的价格是另一份授权，得重新报价。
                # 下一轮循环里 last_quote 会变成新报价，数量就对上了。
                if "get_quote" not in slots.did:
                    return self._call_quote(slots, qty=requested_qty)

            elif not slots.current_turn_results and slots.last_search is not None:
                # 客户在确认，但手上没有可下单的报价 —— 说明上一轮没报成价
                # （缺货或没找到）。
                # ★ 这时候绝不能重新搜一遍：那等于替客户改主意，
                #   而且很可能搜到一件他压根没提过的商品再卖给他。
                #   把上一轮的结论重复一次就够了。
                return self._say(self._unavailable_text(slots))

        # 本轮还没查过货 -> 先查（客户改口时也会走到这里）
        if "search_inventory" not in slots.did:
            return self._call_search(text, slots)

        search = slots.result_of("search_inventory")

        # 查了但没查到 —— 直接把可选选项告诉客户，不要继续往下走。
        # 继续走的话会拿 variant_id=0 去报价，最后回一句莫名其妙的
        # 「暂无可用报价」，把「这个颜色没货」这个真正有用的信息弄丢了。
        if search is not None and not search.get("ok"):
            return self._say(self._not_found_text(slots))

        # 查到了但这个组合卖完了。同样不能继续往下走 ——
        # 报价会返回 out_of_stock，客户听到的还是那句没用的
        # 「暂无可用报价」，而真正该说的是「黑色 XL 卖完了」。
        if search is not None and not self._has_stock(search):
            return self._say(self._sold_out_text(slots))

        # 本轮查完了，按顺序补齐后续步骤
        if "get_product_image" not in slots.did:
            return self._call_image(slots)

        if "get_quote" not in slots.did:
            return self._call_quote(slots)

        # 报价这一步失败了（缺货、变体不存在等）。
        # 必须把原因说清楚再收尾 —— 直接走 _quote_text 的话，
        # 它会拿上一轮那份还没失效的旧报价来回复，客户会看到
        # 一个和当前问题对不上的价格。
        failed_quote = slots.result_of("get_quote")
        if failed_quote is not None and not failed_quote.get("ok"):
            return self._say(self._quote_failed_text(failed_quote))

        return self._say(self._quote_text(slots))

    @staticmethod
    def _quote_failed_text(payload: dict) -> str:
        code = payload.get("error_code")
        if code == "out_of_stock":
            available = payload.get("available_qty") or 0
            if available:
                return (
                    f"这个规格现在只剩 {available} 件了，"
                    f"要的话我按 {available} 件给您报价。"
                )
            return "这个规格刚好卖完了，要不要看看其他颜色或尺码？"
        if code == "variant_not_found":
            return "这个款我们暂时没有，要不要看看别的？"
        return "这个规格暂时报不了价，我帮您转人工确认一下好吗？"

    # ── 各步骤 ────────────────────────────────────────────
    def _call_search(self, text: str, slots: Slots) -> AssistantTurn:
        attrs = self._extract(text, slots)
        return AssistantTurn(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_search_1",
                    name="search_inventory",
                    arguments={
                        "sku": attrs.get("sku"),
                        "color": attrs.get("color"),
                        "size": attrs.get("size"),
                    },
                )
            ],
        )

    def _call_image(self, slots: Slots) -> AssistantTurn:
        variant = self._pick_variant(slots)
        return AssistantTurn(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_image_1",
                    name="get_product_image",
                    arguments={
                        "sku": variant.get("sku") if variant else None,
                        "color": variant.get("color") if variant else None,
                    },
                )
            ],
        )

    def _call_quote(self, slots: Slots, qty: int | None = None) -> AssistantTurn:
        variant = self._pick_variant(slots)
        qty = qty if qty is not None else self._extract_qty(slots.last_user)
        return AssistantTurn(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_quote_1",
                    name="get_quote",
                    arguments={
                        "variant_id": variant.get("variant_id") if variant else 0,
                        "qty": qty,
                        "customer_tier": "ignored",
                    },
                )
            ],
        )

    def _call_create_order(self, slots: Slots) -> AssistantTurn:
        offer = self._best_offer(slots.last_quote)
        return AssistantTurn(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_order_1",
                    name="create_order",
                    arguments={
                        "quote_id": (slots.last_quote or {}).get("data", {}).get("quote_id", ""),
                        "customer": "ignored",
                        "items": [
                            {
                                "variant_id": offer.get("variant_id"),
                                "qty": offer.get("qty"),
                                "offer_id": offer.get("offer_id"),
                            }
                        ]
                        if offer
                        else [],
                        "confirm_note": slots.last_user[:200] or None,
                    },
                )
            ],
        )

    # ── 最终话术 ──────────────────────────────────────────
    # 所有数字都从工具结果里取，Mock 不做任何算术。

    def _quote_text(self, slots: Slots) -> str:
        quote = slots.last_quote or {}
        data = quote.get("data", {})
        offers = data.get("authorized_offers") or []
        if not offers:
            return "抱歉，这个商品暂时没有可用的报价，我帮您转人工确认一下。"

        main = offers[0]
        lines = [
            f"有的！{main.get('product_name', '这款')} "
            f"{main.get('color', '')} {main.get('size', '')} 现货 {main.get('available_qty', 0)} 件。"
            if main.get("available_qty") is not None
            else f"有的！{main.get('product_name', '这款')} {main.get('color', '')} {main.get('size', '')}。",
            f"单价 {main.get('unit_price_display')}",
        ]
        if main.get("discount_pct"):
            lines.append(f"（已为您优惠 {main.get('discount_pct')}%）")

        upsells = data.get("upsell_options") or []
        if upsells:
            alt = upsells[0]
            lines.append(
                f"如果拿 {alt.get('qty')} 件，单价可以到 {alt.get('unit_price_display')}，"
                f"合计 {alt.get('line_total_display')}。"
            )

        if slots.last_image:
            lines.append("商品图片已经发给您了。")
        lines.append("需要我帮您下单吗？")
        return "\n".join(lines)

    def _order_text(self, order_payload: dict) -> str:
        data = order_payload.get("data", {})
        if data.get("deduplicated"):
            return (
                f"您的订单 {data.get('order_no')} 已经生成过了，"
                f"合计 {data.get('total_display')}，不用重复下单。"
            )
        return (
            f"好的，订单已生成：{data.get('order_no')}，"
            f"合计 {data.get('total_display')}。我们尽快为您安排发货。"
        )

    def _unavailable_text(self, slots: Slots) -> str:
        """客户想买但买不了时的统一说法，按上一轮的结论选措辞。"""
        search = slots.result_of("search_inventory") or slots.last_search or {}
        matches = (search.get("data") or {}).get("matches") or []
        if not matches:
            return self._not_found_text(slots)
        if not self._has_stock(search):
            return self._sold_out_text(slots)
        return "这一件暂时下不了单，我帮您转人工确认一下好吗？"

    def _not_found_text(self, slots: Slots) -> str:
        """没有这个颜色/尺码的组合。"""
        search = slots.result_of("search_inventory") or slots.last_search or {}
        options = search.get("available_options") or {}
        parts = []
        if options.get("colors"):
            parts.append("颜色有 " + "、".join(options["colors"]))
        if options.get("sizes"):
            parts.append("尺码有 " + "、".join(options["sizes"]))
        hint = ("；" + "，".join(parts)) if parts else ""
        return f"抱歉，这个组合没有找到{hint}。您看要不要换个颜色或尺码？"

    def _sold_out_text(self, slots: Slots) -> str:
        """有这款，但卖完了 —— 和「没这个组合」是两回事，回复也该不一样。"""
        search = slots.result_of("search_inventory") or slots.last_search or {}
        matches = (search.get("data") or {}).get("matches") or []
        if matches:
            m = matches[0]
            return (
                f"抱歉，{m.get('product_name', '这款')} "
                f"{m.get('color_label', '')}{m.get('size_label', '')} 刚好卖完了。"
                "要不要看看其他颜色或尺码？"
            )
        return "抱歉，这款刚好卖完了，要不要看看别的？"

    @staticmethod
    def _has_stock(search: dict) -> bool:
        matches = (search.get("data") or {}).get("matches") or []
        return any(m.get("in_stock") for m in matches)

    # ── 工具方法 ──────────────────────────────────────────
    @staticmethod
    def _say(text: str) -> AssistantTurn:
        return AssistantTurn(content=text, tool_calls=[])

    @staticmethod
    def _is_confirming(text: str) -> bool:
        if not text or len(text) > 30:
            return False
        if CHANGE_RE.search(text):
            return False
        return bool(CONFIRM_RE.search(text))

    def _extract(self, text: str, slots: Slots) -> dict:
        """从文本里抽 sku / color / size。

        改口场景：客户先说黑色 XL，又说「算了要白色 L」——
        这时文本里同时出现两个颜色。取**最后出现**的那个，
        因为那才是客户最新想要的东西。
        """
        result: dict[str, str | None] = {"sku": None, "color": None, "size": None}

        if self._session_factory is None:
            return result

        from app.services.catalog import extract_attributes

        with self._session_factory() as session:
            attrs = extract_attributes(session, text)
            result["color"] = attrs.get("color")
            result["size"] = attrs.get("size")

        # 款号：文本里直接出现的 SKU
        sku_match = re.search(r"\b([A-Z]+-[A-Z]*-?\d+)\b", text.upper())
        if sku_match:
            result["sku"] = sku_match.group(1)

        # 新句子没提到的维度，沿用上一轮。
        if slots.last_search:
            data = slots.last_search.get("data") or {}
            matches = data.get("matches") or []
            previous_query = data.get("query") or {}

            if matches:
                previous = matches[0]
                if result["color"] is None:
                    result["color"] = previous.get("color")
                if result["size"] is None:
                    result["size"] = previous.get("size")

                # ★ 款号的回落要更谨慎：只有当上一轮**本身就是按款号收窄的**
                #   才继承。
                #
                #   无条件继承会出两个方向的错：
                #   1) 客户问「TSHIRT-001 有黑色XL吗」（售罄）再说「要了」时，
                #      不继承款号 -> 搜索范围从「这一款」扩大到「全库」->
                #      搜到第一个有货的黑色 XL，一件客户从没提过的商品，
                #      然后把它卖给了客户。
                #   2) 客户最初只是宽泛地问「黑色XL有吗」，此后每条消息
                #      都会被悄悄锁死到当时碰巧命中的那一款上。
                #   所以只在上一轮确实指定了款号时才继承。
                if result["sku"] is None and previous_query.get("sku"):
                    result["sku"] = previous_query["sku"]

        return result

    @staticmethod
    def _pick_variant(slots: Slots) -> dict | None:
        search = slots.result_of("search_inventory")
        if search is None:
            search = slots.last_search
        if not search or not search.get("ok"):
            return None
        matches = (search.get("data") or {}).get("matches") or []
        if not matches:
            return None
        # 优先选有货的那条。客户问的尺码没货但有邻近尺码时，
        # 至少后续报价不会直接撞 out_of_stock。
        return next((m for m in matches if m.get("in_stock")), matches[0])

    @staticmethod
    def _best_offer(quote: dict | None) -> dict | None:
        offers = ((quote or {}).get("data") or {}).get("authorized_offers") or []
        return offers[0] if offers else None

    @staticmethod
    def _extract_qty(text: str) -> int:
        match = QTY_RE.search(text or "")
        if match:
            try:
                return max(1, min(999, int(match.group(1))))
            except ValueError:
                return 1
        return 1
