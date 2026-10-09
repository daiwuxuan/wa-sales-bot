"""工具执行器。

每个工具调用开一个独立会话和短事务。工具层只做三件事：
参数校验 -> 调 service -> 序列化结果。
任何业务规则都不在这里实现。

返回给模型的一律是统一信封，永远不返回裸数组或裸数字：
  成功 {"ok": true,  "data": {...}}
  失败 {"ok": false, "error_code": "...", "message": "...", ...}

失败路径绝不带任何价格字段 —— 否则模型会从错误信息里「捡」到
没被授权的数字。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from app.ai.tool_schemas import TOOL_NAMES, TOOL_SCHEMAS
from app.core.database import session_scope
from app.core.errors import DomainError
from app.core.money import to_yuan
from app.models import Customer, Conversation, ProductVariant
from app.services import catalog as catalog_service
from app.services import customers as customer_service
from app.services import inventory as inventory_service
from app.services import orders as order_service
from app.services import pricing as pricing_service
from app.services import quotes as quote_service
from app.tools.context import ToolContext

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 8


# ── 1. search_inventory ────────────────────────────────────
def _search_inventory(args: dict, ctx: ToolContext) -> dict:
    # schema 里写了「至少提供一个条件」，但 schema 约束不住所有情况
    # （模型可能全传 null，Mock 更容易犯）。
    # 全空查询会返回全库前几条，然后模型会随便挑一个 —— 这在「客户说
    # 『要了』」这种没有商品信息的句子上一定会发生，而且挑错款。
    # 与其返回一个看似合理实则随机的结果，不如明确报错让上层去用会话状态。
    if not any((args.get("sku"), args.get("color"), args.get("size"))):
        return {
            "ok": False,
            "error_code": "invalid_argument",
            "message": "至少需要提供 sku / color / size 中的一个条件。"
                       "如果客户没有提到任何商品信息，请先问清楚要哪一款。",
        }

    with session_scope() as db:
        variants = catalog_service.search_variants(
            db,
            sku=args.get("sku"),
            color=args.get("color"),
            size=args.get("size"),
        )

        options = catalog_service.available_options(db)

        if not variants:
            # ★ 查不到时必须回带可选选项，否则模型只能干巴巴说「没有」，
            #   对话就死在这里了。有了这个它能顺势推荐替代款。
            return {
                "ok": False,
                "error_code": "variant_not_found",
                "message": "没有匹配的在售变体",
                "available_options": options,
            }

        matches = []
        for variant in variants[:MAX_SEARCH_RESULTS]:
            inv = variant.inventory
            available = inv.available_qty if inv else 0
            matches.append(
                {
                    "variant_id": variant.id,
                    "sku": variant.product.sku if variant.product else variant.variant_sku,
                    "product_name": variant.product.name if variant.product else "",
                    "color": variant.color_code,
                    "color_label": variant.color_label,
                    "size": variant.size_code,
                    "size_label": variant.size_label,
                    "in_stock": available > 0,
                    "available_qty": available,
                    # 刻意不返回价格 —— 价格只能从 get_quote 拿，
                    # 否则会绕过等级折扣和最低价守护。
                }
            )

        in_stock = [m for m in matches if m["in_stock"]]
        return {
            "ok": True,
            "data": {
                "matches": matches,
                "in_stock_count": len(in_stock),
                # 回显实际应用的条件。下一轮需要知道「这次搜索是不是按款号收窄过」——
                # 是的话后续要沿用款号，不是的话不能凭空把客户锁到某个商品上。
                "query": {
                    "sku": args.get("sku"),
                    "color": args.get("color"),
                    "size": args.get("size"),
                },
            },
            "available_options": options,
        }


# ── 2. get_customer ────────────────────────────────────────
def _get_customer(args: dict, ctx: ToolContext) -> dict:
    # ★ args["phone"] 被刻意忽略。客户打一句「我是VIP」不会改变任何东西。
    with session_scope() as db:
        customer = db.get(Customer, ctx.customer_id)
        if customer is None:
            return {"ok": False, "error_code": "customer_not_found", "message": "客户不存在"}

        return {
            "ok": True,
            "data": {
                "name": customer.name,
                "tier": customer.tier_code,
                "tier_name": customer.tier.name if customer.tier else "零售客户",
                "total_orders": customer.total_orders or 0,
                "total_spent_display": f"¥{to_yuan(customer.total_spent_cents or 0):.2f}",
                # 刻意不返回折扣率 —— 折扣的唯一出口是 get_quote。
                # 两处都返回折扣率，就等于两处可能不一致。
            },
        }


# ── 3. get_quote ───────────────────────────────────────────
def _get_quote(args: dict, ctx: ToolContext) -> dict:
    # ★ args["customer_tier"] 被刻意忽略，等级按会话手机号绑定。
    try:
        variant_id = int(args.get("variant_id") or 0)
        qty = int(args.get("qty") or 1)
    except (TypeError, ValueError):
        return {"ok": False, "error_code": "invalid_argument", "message": "参数格式不正确"}

    if qty < 1:
        return {"ok": False, "error_code": "invalid_argument", "message": "数量必须大于 0"}

    with session_scope() as db:
        variant = catalog_service.get_variant(db, variant_id)
        if variant is None:
            return {
                "ok": False,
                "error_code": "variant_not_found",
                "message": "变体不存在",
                "available_options": catalog_service.available_options(db),
            }

        customer = db.get(Customer, ctx.customer_id)
        if customer is None:
            return {"ok": False, "error_code": "customer_not_found", "message": "客户不存在"}

        available = inventory_service.snapshot_stock(db, variant.id)
        if available <= 0:
            return {
                "ok": False,
                "error_code": "out_of_stock",
                "message": "该变体当前无货",
                "variant_id": variant.id,
                "available_qty": 0,
            }

        offers = pricing_service.compute_offers(
            variant, customer.tier, qty, available_qty=available
        )
        primary, *upsells = offers

        # ★ 只有客户实际要的数量进报价单。
        #   报价是购物车，不是菜单 —— 把加购档位也写进去的话，
        #   quote.total_cents 就变成「1 件 + 3 件」的合计，逻辑上就错了。
        #   加购档位的价格是真算出来的（可以转述），但没有 offer_id，
        #   所以模型没法拿它下单。客户同意加购时重新走一次 get_quote，
        #   让那一档拿到属于自己的授权。
        quote = quote_service.create_quote(
            db,
            customer=customer,
            tier=customer.tier,
            conversation_id=ctx.conversation_id,
            entries=[quote_service.QuoteEntry(variant, primary)],
        )

        def _decorate(offer: pricing_service.Offer) -> dict:
            return {
                **offer.to_tool_payload(),
                "product_name": offer.product_name,
                "color": offer.color,
                "size": offer.size,
                "available_qty": available,
            }

        payload: dict = {
            "quote_id": quote.quote_no,
            "currency": quote.currency,
            "total_cents": quote.total_cents,
            "total_display": f"¥{to_yuan(quote.total_cents):.2f}",
            "expires_in_minutes": 30,
            "authorized_offers": [_decorate(primary)],
            "note": "以上为服务端授权价格，只能原样转述，不得自行计算或折算。",
        }

        if upsells:
            payload["upsell_options"] = [
                {
                    "qty": o.qty,
                    "unit_price_display": f"¥{to_yuan(o.unit_price_cents):.2f}",
                    "line_total_display": f"¥{to_yuan(o.line_total_cents):.2f}",
                    "how_to_order": (
                        f"客户接受这一档时，用数量 {o.qty} 重新调用一次 get_quote，"
                        "拿到新的 offer_id 再下单。"
                    ),
                }
                for o in upsells
            ]

        return {"ok": True, "data": payload}


# ── 4. create_order ────────────────────────────────────────
def _create_order(args: dict, ctx: ToolContext) -> dict:
    # ★ args["customer"] 被刻意忽略；参数里也没有任何价格字段。
    quote_id = (args.get("quote_id") or "").strip()
    items = args.get("items") or []

    if not quote_id:
        return {"ok": False, "error_code": "invalid_argument", "message": "缺少 quote_id"}
    if not items:
        return {"ok": False, "error_code": "invalid_argument", "message": "订单明细为空"}

    with session_scope() as db:
        customer = db.get(Customer, ctx.customer_id)
        if customer is None:
            return {"ok": False, "error_code": "customer_not_found", "message": "客户不存在"}

        quote = quote_service.load_quote(db, quote_id)
        if quote is None:
            return {
                "ok": False,
                "error_code": "quote_expired",
                "message": "报价不存在或已失效，请重新报价",
            }

        order, deduplicated = order_service.create_order(
            db,
            customer=customer,
            conversation_id=ctx.conversation_id,
            quote=quote,
            items=[
                {
                    "variant_id": int(i.get("variant_id") or 0),
                    "qty": int(i.get("qty") or 0),
                    "offer_id": i.get("offer_id"),
                }
                for i in items
            ],
        )
        return order_service.order_to_tool_payload(order, deduplicated)


# ── 5. get_product_image ───────────────────────────────────
def _get_product_image(args: dict, ctx: ToolContext) -> dict:
    sku = (args.get("sku") or "").strip()
    color = args.get("color")

    with session_scope() as db:
        variants = catalog_service.search_variants(db, sku=sku or None, color=color)
        if not variants:
            variants = catalog_service.search_variants(db, sku=sku or None)

        if not variants:
            return {
                "ok": False,
                "error_code": "product_not_found",
                "message": "没有找到这个商品",
            }

        variant = variants[0]
        image_key = catalog_service.find_primary_image_key(variant)

        if not image_key:
            # 商家还没在后台上传图片。明确告诉模型没有图，
            # 它就会正常用文字介绍，而不是编一个图片地址出来。
            return {
                "ok": False,
                "error_code": "no_image",
                "message": "该商品还没有上传图片",
                "sku": sku,
            }

        from app.core.config import settings

        return {
            "ok": True,
            "data": {
                "sku": variant.product.sku if variant.product else sku,
                "product_name": variant.product.name if variant.product else "",
                "color": variant.color_label,
                "image_url": settings.public_url_for(image_key),
                "image_key": image_key,
            },
            "display_hint": "图片会由系统随文字一起发给客户，不要在回复里念出 URL。",
        }


# ── 注册表 ─────────────────────────────────────────────────
DISPATCH: dict[str, Callable[[dict, ToolContext], dict]] = {
    "search_inventory": _search_inventory,
    "get_customer": _get_customer,
    "get_quote": _get_quote,
    "create_order": _create_order,
    "get_product_image": _get_product_image,
}


def _assert_registry_in_sync() -> None:
    """schema 和执行器必须一一对应。

    任何一边加了工具另一边没加，服务直接起不来 ——
    比在演示时才发现「模型调用了一个不存在的工具」要好得多。
    """
    missing = TOOL_NAMES - set(DISPATCH)
    extra = set(DISPATCH) - TOOL_NAMES
    if missing or extra:
        raise RuntimeError(
            f"工具注册表与 schema 不一致：缺少实现 {sorted(missing)}，多余实现 {sorted(extra)}"
        )


_assert_registry_in_sync()


def execute_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    """执行一次工具调用。异常一律转成结构化结果回给模型。

    未知工具名和参数错误也要返回结果，而不是抛 500 ——
    模型看到错误码能自我纠正，看到 500 只能瞎猜。
    """
    started = time.perf_counter()
    handler = DISPATCH.get(name)

    if handler is None:
        logger.warning("模型调用了不存在的工具: %s", name)
        return {
            "ok": False,
            "error_code": "unknown_tool",
            "message": f"没有名为 {name} 的工具",
            "available_tools": sorted(DISPATCH),
        }

    try:
        result = handler(args or {}, ctx)
    except DomainError as exc:
        result = exc.to_payload()
    except Exception as exc:  # noqa: BLE001
        logger.exception("工具 %s 执行失败", name)
        result = {"ok": False, "error_code": "internal_error", "message": str(exc)[:200]}

    result["_duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def numeric_whitelist(payload: Any) -> set[str]:
    """从工具结果里抽出所有数字，用作接地校验的白名单。

    ★ 白名单**只**由工具结果扩充，模型自己说过的话永远不进白名单。
      这是「模型不能说一个工具没给过的数字」的执行机制。
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("_"):
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            found.add(_normalize_number(str(node)))
        elif isinstance(node, str):
            for match in _NUM_IN_TEXT.finditer(node):
                found.add(_normalize_number(match.group(0)))

    walk(payload)
    return found


_NUM_IN_TEXT = re.compile(r"\d+(?:\.\d+)?")


def _normalize_number(token: str) -> str:
    """把数字归一化成可比较的形式：199.00 -> 199，1,199 -> 1199。"""
    cleaned = token.replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
