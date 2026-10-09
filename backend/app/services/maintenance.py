"""数据清理。

放在这里而不是各写各的，是因为 orders 和 quotes 之间有一对**循环外键**：
    orders.quote_id            -> quotes.id
    quotes.consumed_by_order_id -> orders.id

谁先谁后都删不掉。必须先把两边的引用置空，再删行。
seed 脚本和测试夹具都需要这个顺序，抄一份迟早会有一边漏改。
"""

from __future__ import annotations

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.models import (
    AiTurn,
    AttributeAlias,
    Campaign,
    CampaignRecipient,
    ChatLog,
    Conversation,
    Customer,
    CustomerTier,
    Inventory,
    MessageTemplate,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    PushRecommendation,
    Quote,
    QuoteItem,
    ToolCallLog,
)


def wipe_business_data(db: Session) -> None:
    """清空全部业务数据，保留表结构。"""
    # 1) 叶子表：没有别的表引用它们，先删
    for model in (
        CampaignRecipient,
        Campaign,
        PushRecommendation,
        ToolCallLog,
        AiTurn,
        ChatLog,
    ):
        db.execute(delete(model))

    db.execute(delete(OrderItem))
    db.execute(delete(QuoteItem))

    # 2) ★ 打断 orders <-> quotes 的循环引用
    db.execute(update(Quote).values(consumed_by_order_id=None))
    db.execute(update(Order).values(quote_id=None))

    # 3) 现在两张表互不引用，可以删了
    db.execute(delete(Order))
    db.execute(delete(Quote))

    # 4) 会话引用 customer，先于 customer 删
    db.execute(delete(Conversation))

    # 5) 商品侧
    db.execute(delete(Inventory))
    db.execute(delete(ProductVariant))
    db.execute(delete(Product))
    db.execute(delete(AttributeAlias))
    db.execute(delete(MessageTemplate))

    # 6) 客户侧
    db.execute(delete(Customer))
    db.execute(delete(CustomerTier))

    db.flush()
