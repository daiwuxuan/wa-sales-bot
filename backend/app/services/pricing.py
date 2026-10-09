"""★ 定价规则引擎 —— 全系统唯一能算出价格的地方。

架构上的意义：ai/ 目录下的任何代码都 import 不到这个模块里的计算函数。
模型只能拿到 compute_offers() 的输出（一个封闭的、算好的枚举），
没有任何可以参与运算的中间量。所以它不是「被要求不要算价」，
而是「拿不到算价所需的东西」。

这个模块也是唯一知道 floor_price_cents（最低价）的地方。
最低价绝不出现在任何工具返回值里 —— 客户问「你的底价是多少」
问到的是一个模型从没见过的数字。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.money import apply_rate, to_yuan
from app.models import CustomerTier, ProductVariant

# 折扣总封顶（百分比）。任何规则叠加都不会突破这一条。
MAX_TOTAL_DISCOUNT_PCT = 30

# 数量阶梯：买得多再让一点。命中哪个档取最高的那一档。
QUANTITY_BREAKS: tuple[tuple[int, int], ...] = (
    (10, 8),   # 满 10 件，额外 8%
    (5, 5),    # 满 5 件，额外 5%
    (3, 2),    # 满 3 件，额外 2%
)


@dataclass(frozen=True)
class Offer:
    """一个「已授权的报价选项」。

    offer_id 是模型唯一被允许引用的价格标识。模型说「¥199」时，
    服务端能把它对应回这一行，从而保证说的话和库里的数永远一致。
    """

    offer_id: str
    variant_id: int
    sku: str
    product_name: str
    color: str
    size: str
    qty: int
    list_price_cents: int
    unit_price_cents: int
    discount_pct: int
    line_total_cents: int
    floor_hit: bool          # 是否触发了最低价保护（只用于日志，不进模型上下文）

    def to_tool_payload(self) -> dict:
        """给模型看的形态。

        ★ 严格白名单：只输出模型有权转述的字段。
          floor_price_cents / cost / 折扣计算过程一律不在这里出现。
        """
        return {
            "offer_id": self.offer_id,
            "variant_id": self.variant_id,
            "qty": self.qty,
            "unit_price_cents": self.unit_price_cents,
            "unit_price_display": f"¥{to_yuan(self.unit_price_cents):.2f}",
            "list_price_cents": self.list_price_cents,
            "discount_pct": self.discount_pct,
            "line_total_cents": self.line_total_cents,
            "line_total_display": f"¥{to_yuan(self.line_total_cents):.2f}",
        }


def quantity_break_pct(qty: int) -> int:
    for threshold, pct in QUANTITY_BREAKS:
        if qty >= threshold:
            return pct
    return 0


def compute_offer(
    variant: ProductVariant,
    tier: CustomerTier | None,
    qty: int,
    *,
    offer_index: int = 0,
) -> Offer:
    """算出某一个数量下的报价。

    管线：基准价 -> 等级折扣 + 数量阶梯（叠加并封顶）-> 与最低价取大
    """
    if qty < 1:
        raise ValueError("qty must be >= 1")

    list_price = variant.list_price_cents
    tier_pct = tier.default_discount_pct if tier else 0
    qty_pct = quantity_break_pct(qty)

    gross_pct = min(tier_pct + qty_pct, MAX_TOTAL_DISCOUNT_PCT)

    if gross_pct:
        discounted = apply_rate(list_price, Decimal(100 - gross_pct) / Decimal(100))
    else:
        discounted = list_price

    # ★ 最低价守护。折扣再大也不会低于这一条线。
    unit_price = max(discounted, variant.floor_price_cents)
    floor_hit = unit_price > discounted

    # 实际生效的折扣率要按最终成交价回算 —— 触发最低价保护时，
    # 名义折扣和实际折扣不是一回事，界面和报表必须显示真实的那一个。
    effective_pct = (
        int(round((list_price - unit_price) * 100 / list_price)) if list_price else 0
    )
    effective_pct = max(0, effective_pct)

    return Offer(
        offer_id=f"O{offer_index}{variant.id}",
        variant_id=variant.id,
        sku=variant.product.sku if variant.product else variant.variant_sku,
        product_name=variant.product.name if variant.product else "",
        color=variant.color_label,
        size=variant.size_label,
        qty=qty,
        list_price_cents=list_price,
        unit_price_cents=unit_price,
        discount_pct=effective_pct,
        line_total_cents=unit_price * qty,
        floor_hit=floor_hit,
    )


def compute_offers(
    variant: ProductVariant,
    tier: CustomerTier | None,
    qty: int,
    *,
    available_qty: int | None = None,
) -> list[Offer]:
    """算出客户可能感兴趣的一组档位。

    get_quote 返回的是一个**封闭枚举**而不是一个数字，这一点是刻意的：
    模型只能「选择并转述」，不能「计算」。想加价卖没有档位可编，
    想打折也没有中间量可乘。

    除了客户实际问的数量，额外给一档数量阶梯（如果库存够），
    这样模型可以自然地做加购推荐 —— 用的仍然是服务端算好的数字。
    """
    offers = [compute_offer(variant, tier, qty, offer_index=0)]

    # 推荐「下一个够得着的档位」，而不是最高档。
    # 客户问 1 件却推「买 10 件更划算」是骚扰，不是加购。
    for threshold in sorted(t for t, _ in QUANTITY_BREAKS):
        if threshold > qty and (available_qty is None or available_qty >= threshold):
            offers.append(compute_offer(variant, tier, threshold, offer_index=1))
            break

    return offers


def shipping_for(_subtotal_cents: int) -> int:
    """运费规则。一期包邮，接口留在这里方便以后加规则。"""
    return 0
