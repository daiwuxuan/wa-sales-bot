"""报表统计。

★ 所有数字都由 SQL 聚合算出来，AI 只负责把算好的结果写成一段总结。
  「让 AI 分析数据」和「让 AI 编数据」之间的区别，就是这一步。

★ 转化率和复购率的定义写在这里，不要改口径而不改注释：
    询盘转化率 = 有过 AI 会话的客户中，下过单的比例
    报价转化率 = 生成过报价的会话中，最终成单的比例
    复购率     = 下过单的客户中，下单 >= 2 次的比例
  这三个口径都是「客户级」而不是「消息级」—— 消息级的比率会被
  客户多问几句就拉低，看起来像转化变差了，其实什么都没发生。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.money import to_yuan
from app.models import (
    ChatLog,
    Conversation,
    Customer,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Quote,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _range_start(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@router.get("/summary")
def summary(db: Session = Depends(get_db), days: int = Query(default=30, le=365)) -> dict:
    """顶部 KPI。"""
    since = _range_start(days)

    gmv_cents = db.scalar(
        select(func.coalesce(func.sum(Order.total_cents), 0)).where(
            Order.created_at >= since, Order.status != "cancelled"
        )
    ) or 0
    order_count = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.created_at >= since, Order.status != "cancelled"
        )
    ) or 0

    # 询盘转化率：有过会话的客户里，有多少最终下单了
    conversation_customers = db.scalar(
        select(func.count(distinct(Conversation.customer_id))).where(
            Conversation.created_at >= since
        )
    ) or 0
    ordering_customers = db.scalar(
        select(func.count(distinct(Order.customer_id))).where(
            Order.created_at >= since, Order.status != "cancelled"
        )
    ) or 0

    # 报价转化率：生成过报价的会话里，有多少最终成单
    quoted_conversations = db.scalar(
        select(func.count(distinct(Quote.conversation_id))).where(
            Quote.created_at >= since, Quote.conversation_id.is_not(None)
        )
    ) or 0
    converted_conversations = db.scalar(
        select(func.count(distinct(Order.conversation_id))).where(
            Order.created_at >= since, Order.conversation_id.is_not(None),
            Order.status != "cancelled",
        )
    ) or 0

    # 复购率：下过单的客户里，下单 >= 2 次的占比
    repeat_customers = db.scalar(
        select(func.count()).select_from(
            select(Order.customer_id)
            .where(Order.status != "cancelled")
            .group_by(Order.customer_id)
            .having(func.count() >= 2)
            .subquery()
        )
    ) or 0
    all_ordering_customers = db.scalar(
        select(func.count(distinct(Order.customer_id))).where(Order.status != "cancelled")
    ) or 0

    takeover_count = db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.ai_enabled.is_(False))
    ) or 0

    def pct(numerator: int, denominator: int) -> float:
        return round(numerator * 100 / denominator, 1) if denominator else 0.0

    return {
        "range_days": days,
        "gmv_cents": gmv_cents,
        "gmv_display": f"¥{to_yuan(gmv_cents):.2f}",
        "order_count": order_count,
        "avg_order_display": f"¥{to_yuan(gmv_cents // order_count if order_count else 0):.2f}",
        "conversation_customers": conversation_customers,
        "inquiry_conversion_pct": pct(ordering_customers, conversation_customers),
        "quote_conversion_pct": pct(converted_conversations, quoted_conversations),
        "repeat_rate_pct": pct(repeat_customers, all_ordering_customers),
        "repeat_customers": repeat_customers,
        "human_takeover_count": takeover_count,
    }


@router.get("/sales-trend")
def sales_trend(db: Session = Depends(get_db), days: int = Query(default=30, le=365)) -> dict:
    """按天的 GMV 与订单量。

    ★ 返回两个独立序列，前端画成上下两张共享 X 轴的图，
      不要画成双 Y 轴 —— GMV 和订单数量纲不同，双轴会让两条线的
      交叉点看起来有意义，其实没有。
    """
    since = _range_start(days)
    day = func.date(Order.created_at)

    rows = db.execute(
        select(
            day.label("day"),
            func.coalesce(func.sum(Order.total_cents), 0).label("gmv"),
            func.count().label("orders"),
        )
        .where(Order.created_at >= since, Order.status != "cancelled")
        .group_by(day)
        .order_by(day)
    ).all()

    return {
        "series": [
            {"date": str(r.day), "gmv_cents": int(r.gmv), "orders": int(r.orders)}
            for r in rows
        ]
    }


@router.get("/top-skus")
def top_skus(
    db: Session = Depends(get_db),
    days: int = Query(default=30, le=365),
    limit: int = Query(default=10, le=50),
) -> dict:
    """按销售额排序的 SKU。卖得好和卖得差都用这个接口，前端按方向取。"""
    since = _range_start(days)

    rows = db.execute(
        select(
            OrderItem.sku_snapshot,
            OrderItem.name_snapshot,
            func.sum(OrderItem.qty).label("units"),
            func.sum(OrderItem.line_total_cents).label("revenue"),
            func.count(distinct(OrderItem.order_id)).label("orders"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.created_at >= since, Order.status != "cancelled")
        .group_by(OrderItem.sku_snapshot, OrderItem.name_snapshot)
        .order_by(func.sum(OrderItem.line_total_cents).desc())
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "sku": r.sku_snapshot,
                "name": r.name_snapshot,
                "units": int(r.units),
                "revenue_cents": int(r.revenue),
                "revenue_display": f"¥{to_yuan(int(r.revenue)):.2f}",
                "orders": int(r.orders),
            }
            for r in rows
        ]
    }


@router.get("/slow-movers")
def slow_movers(db: Session = Depends(get_db), days: int = Query(default=30, le=365)) -> dict:
    """卖得差的：在售但零成交的商品，按积压库存排序。"""
    since = _range_start(days)

    sold_skus = select(OrderItem.sku_snapshot).join(
        Order, Order.id == OrderItem.order_id
    ).where(Order.created_at >= since, Order.status != "cancelled")

    rows = db.execute(
        select(
            Product.sku,
            Product.name,
            func.coalesce(func.sum(ProductVariant.list_price_cents), 0).label("stock_value"),
            func.count(ProductVariant.id).label("variants"),
        )
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(Product.is_active.is_(True), Product.sku.not_in(sold_skus))
        .group_by(Product.sku, Product.name)
        .order_by(func.count(ProductVariant.id).desc())
        .limit(20)
    ).all()

    return {
        "items": [
            {
                "sku": r.sku,
                "name": r.name,
                "variants": int(r.variants),
                "note": "该区间内零成交",
            }
            for r in rows
        ]
    }


@router.get("/funnel")
def funnel(db: Session = Depends(get_db), days: int = Query(default=30, le=365)) -> dict:
    """询盘 -> 报价 -> 成单 的转化漏斗。"""
    since = _range_start(days)

    inquiries = db.scalar(
        select(func.count()).select_from(ChatLog).where(
            ChatLog.direction == "in", ChatLog.created_at >= since
        )
    ) or 0
    quotes = db.scalar(
        select(func.count()).select_from(Quote).where(Quote.created_at >= since)
    ) or 0
    orders = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.created_at >= since, Order.status != "cancelled"
        )
    ) or 0
    paid = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.created_at >= since, Order.status.in_(("paid", "shipped", "completed"))
        )
    ) or 0

    return {
        "steps": [
            {"key": "inquiry", "label": "客户询盘", "value": inquiries},
            {"key": "quote", "label": "生成报价", "value": quotes},
            {"key": "order", "label": "生成订单", "value": orders},
            {"key": "paid", "label": "已付款", "value": paid},
        ]
    }


@router.get("/price-discrepancies")
def price_discrepancies(db: Session = Depends(get_db), limit: int = Query(default=50, le=200)) -> dict:
    """报价 vs 成交价差异。

    理论上永远是空的（下单必须基于报价）。这张表存在的意义是：
    万一哪天真出现差异，人工能立刻发现，而不是等到客户投诉。
    """
    rows = db.execute(
        select(
            Order.order_no,
            Order.total_cents.label("order_total"),
            Quote.total_cents.label("quote_total"),
        )
        .join(Quote, Quote.id == Order.quote_id)
        .where(Order.total_cents != Quote.total_cents)
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "order_no": r.order_no,
                "order_total_display": f"¥{to_yuan(r.order_total):.2f}",
                "quote_total_display": f"¥{to_yuan(r.quote_total):.2f}",
                "diff_cents": r.order_total - r.quote_total,
            }
            for r in rows
        ],
        "note": "正常情况下应为空。非空说明下单时的重算与报价不一致，需要人工核查。",
    }


@router.get("/ai-vs-human")
def ai_vs_human(db: Session = Depends(get_db), days: int = Query(default=30, le=365)) -> dict:
    """AI 与人工的承接量对比。"""
    since = _range_start(days)

    rows = db.execute(
        select(ChatLog.sent_by, func.count().label("n"))
        .where(ChatLog.direction == "out", ChatLog.created_at >= since)
        .group_by(ChatLog.sent_by)
    ).all()

    return {"series": [{"sent_by": r.sent_by, "count": int(r.n)} for r in rows]}


@router.get("/ai-summary")
def ai_summary(db: Session = Depends(get_db), days: int = Query(default=30, le=365)) -> dict:
    """让 AI 把上面算好的数字写成一段总结。

    ★ 交给模型的是一张已经算好的数字表，不是原始订单。
      模型做的是「组织语言」，不是「计算」。
    """
    from app.ai.factory import get_llm_client
    from app.ai.prompts import SYSTEM_PROMPT

    stats = summary(db=db, days=days)
    top = top_skus(db=db, days=days, limit=5)["items"]
    slow = slow_movers(db=db, days=days)["items"][:5]

    facts = {
        "统计区间": f"最近 {days} 天",
        "成交额": stats["gmv_display"],
        "订单数": stats["order_count"],
        "客单价": stats["avg_order_display"],
        "询盘转化率": f"{stats['inquiry_conversion_pct']}%",
        "报价转化率": f"{stats['quote_conversion_pct']}%",
        "复购率": f"{stats['repeat_rate_pct']}%",
        "人工接管中的会话": stats["human_takeover_count"],
        "畅销款": [f"{i['name']}（{i['units']} 件，{i['revenue_display']}）" for i in top],
        "滞销款": [i["name"] for i in slow],
    }

    prompt = (
        "下面是一份已经用 SQL 统计好的经营数据。请用中文写一段 150 字以内的经营总结，"
        "指出最值得注意的一两个点，并给一条可执行的建议。\n"
        "★ 只能引用下面出现过的数字，不要自己计算任何新的比率或金额。\n\n"
        + "\n".join(f"{k}：{v}" for k, v in facts.items())
    )

    try:
        client = get_llm_client()
        turn = client.complete(system=SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}], tools=[])
        text = turn.content or ""
        mode = client.mode
    except Exception as exc:  # noqa: BLE001
        text = f"AI 总结生成失败：{exc}"
        mode = "error"

    return {"summary": text, "mode": mode, "facts": facts}
