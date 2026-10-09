"""所有模型集中导入。

SQLAlchemy 的 create_all 只认识已经被 import 过的类，
所以这个文件必须把每张表都拉进来，漏一个就会少建一张表。
"""

from app.models.base import Base, utcnow
from app.models.catalog import AttributeAlias, Inventory, Product, ProductVariant
from app.models.conversation import AiTurn, ChatLog, Conversation, ToolCallLog
from app.models.customer import Customer, CustomerTier
from app.models.marketing import (
    Campaign,
    CampaignRecipient,
    MessageTemplate,
    PushRecommendation,
)
from app.models.order import Order, OrderItem
from app.models.quote import Quote, QuoteItem

__all__ = [
    "Base",
    "utcnow",
    "Product",
    "ProductVariant",
    "Inventory",
    "AttributeAlias",
    "Customer",
    "CustomerTier",
    "Quote",
    "QuoteItem",
    "Order",
    "OrderItem",
    "Conversation",
    "ChatLog",
    "AiTurn",
    "ToolCallLog",
    "PushRecommendation",
    "MessageTemplate",
    "Campaign",
    "CampaignRecipient",
]
