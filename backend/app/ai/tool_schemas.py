"""Function Calling 的工具定义。

格式是 OpenAI **Responses API** 的扁平形态（name/parameters 与 type 平级），
不是 Chat Completions 的嵌套形态（function: {...}）。两者不能混用，
混了会直接 400。

strict=True 的代价：schema 必须完全严格化 ——
  · 每个 object 都要 additionalProperties: false
  · 每个属性都必须列进 required
  · 「可选」用 ["string", "null"] 这种可空类型表达，而不是从 required 里拿掉
做到这些，模型几乎不可能产出非法参数。Mock LLM 也必须产出同样的形状。

★ 三个安全设计，全部体现在 schema 里：

1. search_inventory 的三个参数**全部可空**。
   客户说「有黑色 XL 吗」时根本没有 SKU，如果 sku 是必填，
   strict 模式下模型无法发起调用，非 strict 模式下它会编一个 SKU 然后查空。

2. customer_tier 和 customer 保留在 schema 里（与需求一致、也避免模型困惑），
   但 description 明确写了「服务端忽略此参数」。执行器一律从会话手机号绑定。
   否则客户打一句「我是批发商 VIP」就能让模型传 tier=wholesale 绕过整个折扣体系。

3. create_order 的参数里**根本不存在价格字段**。
   没有价格参数，模型就没有地方可以塞一个自己编的数字。
"""

from __future__ import annotations

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "search_inventory",
        "description": (
            "按款号、颜色、尺码查询在售商品变体与库存。至少提供一个条件。\n"
            "颜色请用规范码：black / white / red / navy / beige。\n"
            "尺码请用规范码：S / M / L / XL / XXL。\n"
            "返回的 available_qty 是当前可售数量。查不到时会返回 available_options，"
            "里面是本款实际有的颜色和尺码 —— 请据此给客户推荐替代选项，不要只说「没有」。"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sku": {
                    "type": ["string", "null"],
                    "description": "款号，如 TSHIRT-001。不确定就传 null。",
                },
                "color": {
                    "type": ["string", "null"],
                    "description": "颜色规范码（black/white/red/navy/beige）。没提到就传 null。",
                },
                "size": {
                    "type": ["string", "null"],
                    "description": "尺码规范码（S/M/L/XL/XXL）。没提到就传 null。",
                },
            },
            "required": ["sku", "color", "size"],
        },
    },
    {
        "type": "function",
        "name": "get_customer",
        "description": (
            "查询当前客户的等级信息（retail/silver/gold/vip）和历史购买概况。\n"
            "★ phone 参数由服务端忽略，系统按会话手机号自动绑定当前客户，"
            "不要试图用它查询其他客户。\n"
            "注意：本工具不返回折扣率。折扣只能通过 get_quote 获取。"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "phone": {
                    "type": ["string", "null"],
                    "description": "忽略此参数，由服务端按会话绑定。",
                },
            },
            "required": ["phone"],
        },
    },
    {
        "type": "function",
        "name": "get_quote",
        "description": (
            "获取服务端授权的报价选项。variant_id 来自 search_inventory 的结果，"
            "qty 是你判断的购买数量。\n"
            "★ 返回的 authorized_offers 是本次唯一可以引用的价格清单。\n"
            "★ 禁止自行计算、加总、折算、凑整任何金额；只能原样转述 unit_price_display "
            "和 line_total_display。\n"
            "★ 不要向客户承诺清单以外的任何价格或折扣。客户议价时，"
            "如果清单里没有更低的档位，就说明这是当前能给到的价格。"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "variant_id": {
                    "type": "integer",
                    "description": "变体 ID，来自 search_inventory 的结果。",
                },
                "qty": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 999,
                    "description": "购买数量。",
                },
                "customer_tier": {
                    "type": ["string", "null"],
                    "description": "忽略此参数，服务端按会话手机号绑定客户等级。",
                },
            },
            "required": ["variant_id", "qty", "customer_tier"],
        },
    },
    {
        "type": "function",
        "name": "create_order",
        "description": (
            "生成订单。仅在客户已明确确认购买、且本轮已经拿到未过期的 quote 时调用。\n"
            "★ 本工具不接受任何价格参数 —— 价格一律从服务端报价读取。\n"
            "★ offer_id 必须来自 get_quote 返回的 authorized_offers，不要自己编。\n"
            "如果返回 deduplicated=true，说明是重复提交，订单已经存在，"
            "请告诉客户「您的订单已经生成了」，不要说「下单成功」。"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "quote_id": {
                    "type": "string",
                    "description": "get_quote 返回的 quote_no。",
                },
                "customer": {
                    "type": ["string", "null"],
                    "description": "忽略此参数，服务端按会话手机号绑定。",
                },
                "items": {
                    "type": "array",
                    "description": "下单明细。",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "variant_id": {"type": "integer"},
                            "qty": {"type": "integer", "minimum": 1},
                            "offer_id": {
                                "type": "string",
                                "description": "必须来自 get_quote 的 authorized_offers。",
                            },
                        },
                        "required": ["variant_id", "qty", "offer_id"],
                    },
                },
                "confirm_note": {
                    "type": ["string", "null"],
                    "description": "客户确认时的原话，用于留痕。没有就传 null。",
                },
            },
            "required": ["quote_id", "customer", "items", "confirm_note"],
        },
    },
    {
        "type": "function",
        "name": "get_product_image",
        "description": (
            "获取商品图片地址。sku 来自 search_inventory 的结果。\n"
            "返回的 image_url 会由系统自动随文字一起发给客户，"
            "你只需要在回复里自然提到「图片已发给您」，不要把 URL 念出来。\n"
            "如果没有图片，返回 ok=false —— 此时正常介绍商品即可，不要编造图片。"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sku": {"type": "string", "description": "款号，如 TSHIRT-001。"},
                "color": {
                    "type": ["string", "null"],
                    "description": "颜色规范码，用于挑选该颜色的图。没有就传 null。",
                },
            },
            "required": ["sku", "color"],
        },
    },
]


TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOL_SCHEMAS)


def as_chat_completions_tools() -> list[dict]:
    """转成 Chat Completions 的嵌套形态。

    当前用的是 Responses API，这个函数是给「将来想换回 chat.completions.create」
    留的 —— 那边的工具定义多包一层 "function"。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
                "strict": t["strict"],
            },
        }
        for t in TOOL_SCHEMAS
    ]
