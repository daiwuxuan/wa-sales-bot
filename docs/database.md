# 数据库表设计

18 张表。分五组：商品库存、客户、报价订单、会话日志、营销。

## 两条贯穿全局的约定

**1. 金额一律用整数分存储**（`*_cents`）。

SQLAlchemy 的 `Numeric` 在 SQLite 上会走浮点并抛
`SAWarning: Dialect sqlite+pysqlite does *not* support Decimal objects natively`，
`0.1 + 0.2 != 0.3` 会直接体现在报价总额上。整数分在 SQLite 和 Postgres 上行为完全一致，
切库零风险。全系统唯一的元↔分转换入口是 `app/core/money.py`。

**2. 时间一律存 UTC。**

报表按本地日聚合时在 Python 侧算好区间边界再传 UTC 值查询
（SQLite 没有 `AT TIME ZONE`），否则「今日 GMV」会错 8 小时。

---

## 一、商品与库存

### SKU 为什么分两层

```
products.sku = "TSHIRT-001"          款号（SPU 语义）
   └── product_variants               颜色 × 尺码组合，价格挂在这一层
          └── inventory               库存挂在这一层，一变体一行
```

因为 `search_inventory(sku, color, size)` 的语义是「某商品的某颜色某尺码」。
价格和库存都可能因颜色尺码而异，订单行也必须指向具体 variant 才能精确扣减。
把 color/size 直接塞进 inventory 的话，将来要给变体加重量、条码就无处安放。

### `products` 商品

| 字段 | 类型 | 约束 |
|---|---|---|
| id | INTEGER | PK |
| sku | TEXT(64) | **UNIQUE**, index |
| name | TEXT(200) | NOT NULL |
| description | TEXT | |
| category | TEXT(64) | index |
| brand | TEXT(64) | |
| **image_url** | TEXT(500) | 商家上传的商品主图 |
| is_active | BOOLEAN | default true, index |
| created_at / updated_at | DATETIME | UTC |

> `image_url` 存**根相对路径**（`/uploads/ab12.jpg`），不存完整 URL ——
> 换域名或上 CDN 时要改的是配置，不是每一行数据。

### `product_variants` 变体

| 字段 | 类型 | 约束 |
|---|---|---|
| id | INTEGER | PK |
| product_id | FK products.id | ON DELETE CASCADE, index |
| variant_sku | TEXT(80) | **UNIQUE**（如 `TSHIRT-001-BLK-XL`）|
| color_code / color_label | TEXT | 规范码 `black` / 展示名 `黑色` |
| size_code / size_label | TEXT | 规范码 `XL` / 展示名 `XL` |
| list_price_cents | INTEGER | NOT NULL, **CHECK >= 0** |
| **floor_price_cents** | INTEGER | NOT NULL, **CHECK >= 0** ← 最低价 |
| weight_grams | INTEGER | |
| is_active | BOOLEAN | default true, index |

**UNIQUE(product_id, color_code, size_code)** ← `search_inventory` 的主查询索引

> `floor_price_cents` 是硬底线，**永远不会进入模型上下文**。
> 客户问「你的底价是多少」，问到的是一个模型从没见过的数字。

### `inventory` 库存

| 字段 | 类型 | 约束 |
|---|---|---|
| id | INTEGER | PK |
| variant_id | FK product_variants.id | **UNIQUE**, index |
| qty_on_hand | INTEGER | **CHECK >= 0** ← 防超卖最后一道墙 |
| qty_reserved | INTEGER | **CHECK >= 0** |
| low_stock_threshold | INTEGER | default 5 |

可售数量 = `qty_on_hand - qty_reserved`（`Inventory.available_qty` 属性）。

### `attribute_aliases` 属性别名

| 字段 | 说明 |
|---|---|
| attr_type | `color` / `size` |
| alias | 归一化后的输入（小写去空格）：`黑色`、`黑`、`加大` |
| canonical | 规范码：`black`、`XL` |
| label | 展示名 |

> **这张表是必需的，不是可选项。** 客户永远说中文，DB 永远存英文码。
> 没有这一层归一化，`search_inventory` 会大面积查空，主链路第一步就断。

---

## 二、客户

### `customers`

| 字段 | 类型 | 说明 |
|---|---|---|
| phone | TEXT(32) | **UNIQUE**, index（E.164 不带 `+`）|
| name | TEXT(120) | |
| tier_id | FK customer_tiers.id | index |
| total_orders / total_spent_cents | INTEGER | 报表用 |
| is_blacklisted | BOOLEAN | 退订/拉黑，群发时排除 |
| first_seen_at / last_seen_at | DATETIME | |

### `customer_tiers` 等级

| 字段 | 说明 |
|---|---|
| code | **UNIQUE**：retail / silver / gold / vip |
| name | 展示名 |
| default_discount_pct | 折扣百分点（5 = 95 折）|
| sort_order / is_active | |

> 新客户一律 `retail`，**等级绝不从消息内容推断**。
> 客户打一句「我是 VIP」不会改变任何东西，提权必须走后台。

---

## 三、报价与订单

### 为什么要有独立的 `quotes` 表

报价是「一次购买意图」的凭证。`get_quote` 返回的不是一个价格公式，而是规则引擎
预先算好的**有限枚举**（`authorized_offers`），每一档带 `offer_id` 和算好的单价。
模型只能**选择并原样转述**，不能计算 —— 它拿到的本来就是封闭列表，没有任何可运算的中间量。

下单时按 `offer_id` 从 `quote_items` 读回单价重新校验，
所以**模型在聊天里说的价格和订单里的价格不可能脱钩** —— 它们来自同一行数据。

### `quotes`

| 字段 | 说明 |
|---|---|
| quote_no | **UNIQUE**，`Q20261008-0001` |
| customer_id / conversation_id | FK |
| customer_tier_code | **快照**，事后客户升级等级也能还原当时为什么报这个价 |
| subtotal_cents / discount_total_cents / total_cents | 全部服务端算 |
| status | active / consumed / expired / cancelled |
| consumed_by_order_id | FK orders.id，幂等锚点 |
| rules_applied_json | 命中了哪些规则，审计用 |
| expires_at | 默认 30 分钟 |

### `quote_items` = 一个「已授权的报价选项」

| 字段 | 说明 |
|---|---|
| quote_id / variant_id | FK |
| sku_snapshot / name_snapshot / color_snapshot / size_snapshot | 快照，防商品改名后对不上 |
| qty / list_price_cents / unit_price_cents / discount_pct / line_total_cents | |
| floor_price_cents | 快照，审计用，**不进模型上下文** |
| **offer_id** | **模型唯一被允许引用的价格标识** |

**UNIQUE(quote_id, offer_id)**

> 加购档位（`upsell_options`）**不进 quote_items**，因为它没有 `offer_id`，
> 模型无法拿它下单。报价是购物车不是菜单 —— 全放进去的话
> `total_cents` 会变成「1 件 + 3 件」的合计，逻辑上就错了。
> 客户接受加购时重新走一次 `get_quote`，让那一档拿到自己的授权。

### `orders`

| 字段 | 说明 |
|---|---|
| order_no | **UNIQUE**，`SO20261008-0001` |
| **idempotency_key** | **UNIQUE**, index ← 幂等仲裁者 |
| customer_id / customer_phone | FK + 快照 |
| conversation_id / quote_id | FK |
| status | pending_confirm / confirmed / paid / shipped / completed / cancelled |
| source | ai / manual / mock / broadcast |
| subtotal_cents / discount_total_cents / shipping_cents / total_cents | |
| deduplicated_from_id | FK orders.id |

索引：`(customer_id, created_at)`、`(status, created_at)`

### `order_items`

除快照字段外，关键约束：

```sql
CHECK (unit_price_cents >= floor_price_cents)   -- 低于最低价的订单行物理上写不进库
CHECK (qty > 0)
```

`quote_item_offer_id` 保留溯源：这一行来自哪个报价选项。

---

## 四、会话与 AI 日志

### `conversations`

| 字段 | 说明 |
|---|---|
| external_thread_id | **UNIQUE**，客户手机号 |
| **ai_enabled** | **人工接管开关** —— AI 调用前必须先看这个字段 |
| assigned_to / takeover_at / takeover_reason | |
| last_inbound_at / session_window_expires_at | 后者 = 客户最后一条消息 + 24h |
| **state_json** | **跨轮次的会话状态**（上一轮的报价、变体、图片、订单）|

> `state_json` 是必需的。`chat_logs` 只存最终文本，工具结果在回合结束就丢了。
> 没有它，下一轮模型根本不知道「上一轮已经报过价」，
> 客户说「要了」时它只能重新查一遍货 —— 而且很可能查到别的变体。
> 真实模型和 Mock 都读这个字段。

### `chat_logs`

| 字段 | 说明 |
|---|---|
| direction | `in` / `out` |
| message_type | text / image / template / interactive |
| body / media_url | |
| **wa_message_id** | 见下方部分唯一索引 |
| sent_by | ai / human / system |

```sql
CREATE UNIQUE INDEX uq_chat_logs_wa_message_id ON chat_logs(wa_message_id)
  WHERE wa_message_id IS NOT NULL;
```

> **这一条索引就解决了 webhook 重复投递。** WhatsApp 在你超时或返回非 2xx 时
> 会重投同一条消息，不拦的话会触发第二整个 AI turn —— 客户收到两条回复，
> 如果那条消息是「要了」，还会多出一张订单。
> 部分索引只约束非空值，所以出站消息（没有 wamid）不受影响。
> SQLite 和 Postgres 都支持。

### `ai_turns` —— 一次客户消息 = 一个可回放的单元

`id`(UUID) / `conversation_id` / `llm_mode` / `model` / `status` /
`final_text` / `guard_violations_json` / `allowed_numbers_json` /
`prompt_tokens` / `completion_tokens` / `latency_ms`

### `tool_call_logs` —— 「AI 做了什么」的唯一事实来源

`turn_id` / `step_index` / `tool_name` / `arguments_json` / `result_json` /
`result_ok` / `error_code` / `duration_ms`

索引：`(turn_id, step_index)`

---

## 五、营销

### `push_recommendations` 推送审核

`customer_id` / `interest` / `history_summary` / `recommended_sku` /
`recommended_variant_id` / `ai_copy` / `edited_copy` /
`status`(pending/sent/skipped/edited) / `decided_by` / `decided_at`

### `message_templates`

`name` / `category` / `body_template` / `variables_json` /
`wa_template_name` / `wa_template_status`(none/pending/approved/rejected)

> 真实 WhatsApp 在 24 小时客服窗口外**只允许发预审批模板**，自由文本会被拒。
> 字段现在就建好，二期填上 `wa_template_name` 就能用。

### `campaigns` / `campaign_recipients`

campaigns：`name` / `message_text` / `image_url` / `audience_filter_json` /
`status`(draft/pending_approval/sending/paused/done) / `throttle_per_minute` /
`total_targets` / `sent_count` / `failed_count`

campaign_recipients：`campaign_id` / `customer_id` / `rendered_body` /
`status`(pending/sent/delivered/read/failed/**opted_out**) / `wa_message_id` / `error`

**UNIQUE(campaign_id, customer_id)**，索引 `(campaign_id, status)`

> `opted_out` 状态对应 `customers.is_blacklisted`。真实环境给退订客户群发会封号，
> 这条规则虽然是 Mock 阶段也照常执行 —— 免得接入真实 API 时才想起。

---

## 循环外键

`orders` 和 `quotes` 互相引用：

```
orders.quote_id             -> quotes.id
quotes.consumed_by_order_id -> orders.id
```

**清表时必须先打断循环**，否则两张表谁也删不掉：

```python
db.execute(update(Quote).values(consumed_by_order_id=None))
db.execute(update(Order).values(quote_id=None))
db.execute(delete(Order)); db.execute(delete(Quote))
```

这段逻辑在 `app/services/maintenance.py` 里，`seed.py` 和测试夹具共用同一份 ——
抄两份迟早会有一边漏改。
