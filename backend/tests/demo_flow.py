"""端到端演示脚本：跑完整个销售链路并打印轨迹。

用法（先启动服务）：
    .venv/Scripts/python.exe tests/demo_flow.py

它是脚本而不是 pytest 用例，因为要连真实跑着的 HTTP 服务，
看到完整的对话流和 AI 轨迹 —— 这是给人看的，不是给 CI 看的。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
PHONE = "8613600136000"


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "body": exc.read().decode("utf-8", "replace")}


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def say(text: str, message_id: str | None = None) -> dict:
    payload = {"phone": PHONE, "text": text}
    if message_id:
        payload["message_id"] = message_id
    return post("/api/mock-webhook", payload)


def show(result: dict) -> None:
    if "_http_error" in result:
        print(f"  HTTP {result['_http_error']}: {result['body'][:200]}")
        return

    if result.get("deduplicated"):
        print("  [重投，已忽略]")
        return

    for step in result.get("steps", []):
        args = json.dumps(step["args"], ensure_ascii=False)
        mark = "OK " if step["ok"] else "ERR"
        print(f"  {mark} {step['tool']}({args}) {step['duration_ms']}ms")

    if result.get("guard", {}).get("violations"):
        print(f"  !! 接地校验拦截: {result['guard']['violations']}")

    print(f"  AI: {result.get('reply_text')}")


def main() -> int:
    print("=" * 70)
    print("调试信息")
    print("=" * 70)
    mode = get("/api/llm/mode")
    print(f"  LLM 模式: {mode['mode']} ({mode['model']})")

    print()
    print("=" * 70)
    print("场景 1：客户问「有黑色XL吗」 -> 查库存 -> 取图 -> 报价")
    print("=" * 70)
    print(f"客户: 有黑色XL吗")
    r1 = say("有黑色XL吗")
    show(r1)

    quote_id = None
    for step in r1.get("steps", []):
        if step["tool"] == "get_quote" and step["ok"]:
            pass

    print()
    print("=" * 70)
    print("场景 2：客户确认下单 -> create_order")
    print("=" * 70)
    print("客户: 要了")
    r2 = say("要了")
    show(r2)

    print()
    print("=" * 70)
    print("场景 3：重投同一条 webhook（幂等）")
    print("=" * 70)
    print("(用相同的 message_id 重发场景 2 的消息)")
    r3 = say("要了", message_id="demo-fixed-message-id-001")
    show(r3)
    print("重发同一 message_id:")
    r4 = say("要了", message_id="demo-fixed-message-id-001")
    show(r4)

    print()
    print("=" * 70)
    print("订单")
    print("=" * 70)
    orders = get("/api/orders")
    for order in orders["items"]:
        print(f"  {order['order_no']}  {order['total_display']}  状态={order['status']}  来源={order['source']}")
        for item in order["items"]:
            print(
                f"      {item['name']} {item['color']}{item['size']} ×{item['qty']}"
                f"  成交 {item['unit_price_display']}  最低价 {item['floor_price_display']}"
            )

    print()
    print("=" * 70)
    print("AI 处理轨迹（从日志里回放）")
    print("=" * 70)
    turns = get(f"/api/conversations/{r1.get('conversation_id', 1)}/turns")
    for turn in turns["items"][:3]:
        detail = get(f"/api/ai/turns/{turn['turn_id']}")
        print(f"  turn {turn['turn_id'][:8]}  状态={detail['status']}  模型={detail['model']}")
        for step in detail["steps"]:
            print(f"      step{step['step']} {step['tool']} ok={step['ok']}")
        print(f"      白名单数字: {detail['allowed_numbers'][:12]}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
