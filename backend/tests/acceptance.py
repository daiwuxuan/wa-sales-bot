"""端到端验收：对着真实跑着的 HTTP 服务打。

前面的 pytest 是在进程内直接调函数。这个脚本打的是真接口 ——
包括对「下单」这个端点做并发攻击，验证防超卖在完整链路上（HTTP ->
工具层 -> 服务层 -> 数据库锁）依然成立，而不只是在单元测试里成立。

用法（先起好后端）：
    .venv/Scripts/python.exe tests/acceptance.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:8000"

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}  {detail}")


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def patch(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    print("=" * 72)
    print("验收 1：完整销售链路")
    print("=" * 72)
    phone = "8613600136000"
    r1 = post("/api/mock-webhook", {"phone": phone, "text": "有黑色XL吗"})
    tools = [s["tool"] for s in r1.get("steps", [])]
    check("查库存被调用", "search_inventory" in tools, str(tools))
    check("取商品图被调用", "get_product_image" in tools, str(tools))
    check("报价被调用", "get_quote" in tools, str(tools))
    check("回复里含价格", "¥" in (r1.get("reply_text") or ""), r1.get("reply_text", "")[:60])
    check("接地校验未拦截", not r1.get("guard", {}).get("violations"))

    r2 = post("/api/mock-webhook", {"phone": phone, "text": "要了"})
    order_tools = [s["tool"] for s in r2.get("steps", [])]
    check("确认后生成订单", "create_order" in order_tools, str(order_tools))

    orders = get("/api/orders")["items"]
    check("订单出现在后台", len(orders) >= 1)
    if orders:
        order = orders[0]
        check("订单金额为正", order["total_cents"] > 0, str(order["total_cents"]))
        check(
            "最低价可见于后台",
            all("floor_price_display" in i for i in order["items"]),
        )

    print()
    print("=" * 72)
    print("验收 2：幂等（同一条 webhook 重投）")
    print("=" * 72)
    mid = f"acc-{uuid.uuid4().hex[:12]}"
    post("/api/mock-webhook", {"phone": phone, "text": "要了", "message_id": mid})
    before = len(get("/api/orders")["items"])
    r_dup = post("/api/mock-webhook", {"phone": phone, "text": "要了", "message_id": mid})
    after = len(get("/api/orders")["items"])
    check("重投被识别", r_dup.get("deduplicated") is True, str(r_dup)[:100])
    check("重投没有产生新订单", before == after, f"{before} -> {after}")

    print()
    print("=" * 72)
    print("验收 3：人工接管后 AI 停止回复")
    print("=" * 72)
    convs = get("/api/conversations")["items"]
    conv_id = convs[0]["id"]
    patch(f"/api/conversations/{conv_id}/takeover", {"enabled": True, "by": "验收脚本"})
    r_takeover = post("/api/mock-webhook", {"phone": phone, "text": "在吗"})
    check("接管后 AI 不介入", r_takeover.get("ai_enabled") is False, str(r_takeover)[:100])
    check("接管后没有工具调用", not r_takeover.get("steps"))
    patch(f"/api/conversations/{conv_id}/takeover", {"enabled": False, "by": "验收脚本"})

    print()
    print("=" * 72)
    print("验收 4：防超卖 —— 对真实 HTTP 端点做并发下单")
    print("=" * 72)

    # 必须精确定位到买家会问到的那一个变体，否则买家买了一款、
    # 我们盯着另一款的库存看，结论是假的。
    # 做法：锁定某一款的黑色 XL，并让买家在消息里带上款号 ——
    # search_inventory 会按 sku 收窄到这一款，10 个人抢的就是同一行库存。
    products = get("/api/products")["items"]
    target = None
    product_sku = None
    for product in products:
        for variant in product["variants"]:
            if variant["color_code"] == "black" and variant["size_code"] == "XL":
                target, product_sku = variant, product["sku"]
                break
        if target:
            break

    if target is None:
        print("  跳过：找不到黑色 XL 变体")
    else:
        # 注意 target 是「变体」，它没有 sku 字段 —— 商品号要单独记下来
        variant_id = target["id"]
        patch(
            f"/api/products/variants/{variant_id}",
            {
                "qty_on_hand": 1,
                "list_price": target["list_price_cents"] / 100,
                "floor_price": min(target["floor_price_cents"], target["list_price_cents"]) / 100,
            },
        )
        print(f"  目标变体 {target['variant_sku']}（{product_sku}）库存已设为 1")

        # 10 个不同客户同时抢同一款的黑色 XL。
        # 消息里带款号是为了让 search_inventory 收窄到那一款 ——
        # 否则买家可能买到别的商品，测出来的结论是假的。
        ask = f"{product_sku} 有黑色XL吗"

        def buy(index: int) -> bool:
            buyer = f"8613800{index:05d}"
            try:
                post("/api/mock-webhook", {"phone": buyer, "text": ask})
                result = post("/api/mock-webhook", {"phone": buyer, "text": "要了"})
                return any(
                    s["tool"] == "create_order" and s["ok"] for s in result.get("steps", [])
                )
            except Exception:  # noqa: BLE001
                return False

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(buy, range(10)))

        winners = sum(1 for r in results if r)
        print(f"  10 个客户并发抢 1 件 -> {winners} 单成功")

        after_stock = get(f"/api/products/{product_sku}")["variants"]
        remaining = next(
            (v["qty_on_hand"] for v in after_stock if v["id"] == variant_id), None
        )

        check("成功下单数不超过库存数", winners <= 1, f"{winners} 单成功，库存只有 1")
        check("库存没有变成负数", remaining is not None and remaining >= 0, f"剩余 {remaining}")
        check("库存被如实扣减", remaining == 1 - winners, f"剩余 {remaining}，预期 {1 - winners}")

    print()
    print("=" * 72)
    print("验收 5：AI 日志可回放")
    print("=" * 72)
    turns = get(f"/api/conversations/{conv_id}/turns")["items"]
    check("产生了 AI turn 记录", len(turns) > 0)
    if turns:
        detail = get(f"/api/ai/turns/{turns[0]['turn_id']}")
        check("turn 有工具调用步骤", len(detail["steps"]) > 0)
        check("白名单数字已落库", len(detail["allowed_numbers"]) > 0)

    print()
    print("=" * 72)
    print(f"结果：{passed} 项通过，{failed} 项失败")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
