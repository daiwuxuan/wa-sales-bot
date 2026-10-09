"""防超卖。

这组测试是整个项目里最重要的验收项 —— 库存扣减错了，
表现是「卖出去的东西发不出货」，而且往往在演示当天才发现。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.database import session_scope
from app.core.errors import OutOfStock
from app.models import Inventory
from app.services import inventory as inventory_service
from app.services.inventory import StockLine


def _stock(variant_id: int) -> int:
    with session_scope() as session:
        value = session.scalar(
            select(Inventory.qty_on_hand).where(Inventory.variant_id == variant_id)
        )
        return value if value is not None else -999


def _attempt_buy(variant_id: int, qty: int) -> str:
    """模拟一次下单的扣减。每个线程走自己独立的会话和连接。"""
    try:
        with session_scope() as session:
            inventory_service.deduct_locked(session, [StockLine(variant_id, qty)])
        return "ok"
    except OutOfStock:
        return "out_of_stock"
    except OperationalError as exc:
        # SQLite 在锁等待超时后会抛这个。真发生说明 busy_timeout 不够，
        # 应该算作失败而不是静默成功。
        return f"locked:{exc.orig}"


def test_last_item_goes_to_exactly_one_buyer(make_variant):
    """10 个线程抢最后 1 件，只能有 1 个成功，且库存停在 0（不是 -1）。"""
    variant_id = make_variant(stock=1)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: _attempt_buy(variant_id, 1), range(10)))

    ok = [r for r in results if r == "ok"]
    assert len(ok) == 1, f"应恰好 1 单成功，实际 {len(ok)} 单：{results}"
    assert _stock(variant_id) == 0, "库存必须停在 0"


def test_stock_never_goes_negative(make_variant):
    """并发冲击下库存必须恒 >= 0。CHECK 约束是最后一道防线。"""
    variant_id = make_variant(stock=3)

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: _attempt_buy(variant_id, 1), range(12)))

    remaining = _stock(variant_id)
    assert remaining >= 0, f"库存变成负数了：{remaining}"
    assert remaining <= 3


def test_multi_line_order_is_all_or_nothing(make_variant):
    """一张单里有一行库存不够，整单必须失败 —— 不能只发一半货。"""
    plenty = make_variant(sku="A-001", stock=10)
    scarce = make_variant(sku="B-001", stock=1)

    with pytest.raises(OutOfStock):
        with session_scope() as session:
            inventory_service.deduct_locked(
                session, [StockLine(plenty, 2), StockLine(scarce, 5)]
            )

    # 前一行虽然够，但整单回滚了，不能被扣掉
    assert _stock(plenty) == 10, "整单失败时不能扣掉任何一行的库存"
    assert _stock(scarce) == 1


def test_sequential_oversell_is_rejected(make_variant):
    """单线程下的基本正确性：买超了要报 out_of_stock 并带上真实可售数。"""
    variant_id = make_variant(stock=2)

    with pytest.raises(OutOfStock) as exc_info:
        with session_scope() as session:
            inventory_service.deduct_locked(session, [StockLine(variant_id, 3)])

    payload = exc_info.value.to_payload()
    assert payload["error_code"] == "out_of_stock"
    # 可售数要回给模型，它才能说「只剩 2 件了，要吗」
    assert payload["available_qty"] == 2
    assert _stock(variant_id) == 2


def test_restock_returns_inventory(make_variant):
    """取消订单要能回补，且回补走同一套加锁路径。"""
    variant_id = make_variant(stock=5)

    with session_scope() as session:
        inventory_service.deduct_locked(session, [StockLine(variant_id, 3)])
    assert _stock(variant_id) == 2

    with session_scope() as session:
        inventory_service.restock_locked(session, [StockLine(variant_id, 3)])
    assert _stock(variant_id) == 5
