"""库存扣减。防超卖的三道墙都在这里。

第一道（SQLite）：core/database.py 的 BEGIN IMMEDIATE，事务第一个语句就拿到写锁，
                   并发写事务在数据库层被串行化。
第一道（Postgres）：lock_variants() 里的 SELECT ... FOR UPDATE。
第二道：条件 UPDATE —— WHERE 里带 qty 判断，靠 rowcount 判定成败。这一条
        在两个数据库上都成立，是真正的兜底。
第三道：inventory 表上的 CHECK (qty_on_hand >= 0)。即使前两道都因为某个
        没想到的路径失效了，负库存也写不进库。

★ 绝不在持有锁的时候调用 LLM。
  一次 GPT-4o 往返 1~5 秒，期间持有 BEGIN IMMEDIATE 会让整库（含所有后台页面）
  卡死。所以「查库存」是不加锁的软检查，「扣库存」才是加锁的硬检查。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import OutOfStock
from app.models import Inventory


@dataclass(frozen=True)
class StockLine:
    variant_id: int
    qty: int


def lock_variants(db: Session, variant_ids: list[int]) -> dict[int, Inventory]:
    """锁住涉及到的库存行。

    Postgres 走 SELECT ... FOR UPDATE；SQLite 是空操作 —— 写锁在事务开始时
    就已经由 BEGIN IMMEDIATE 拿到了，行锁是冗余的。
    """
    ids = sorted(set(variant_ids))  # ★ 固定顺序，防止多行加锁时发生死锁
    if not ids:
        return {}

    stmt = select(Inventory).where(Inventory.variant_id.in_(ids))
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    return {inv.variant_id: inv for inv in db.scalars(stmt)}


def deduct_one(db: Session, variant_id: int, qty: int) -> None:
    """扣减单行库存。条件写在 WHERE 里，成败看 rowcount。

    这一个写法同时解决两个数据库的问题：
      Postgres —— UPDATE ... WHERE qty >= n 在 READ COMMITTED 下会等行锁，
                  拿到锁后重新求值 WHERE，并发的第二个事务会看到已提交的新值，
                  rowcount=0 -> 抛 OutOfStock。
      SQLite   —— 写锁串行化，效果相同。
    """
    result = db.execute(
        update(Inventory)
        .where(
            Inventory.variant_id == variant_id,
            (Inventory.qty_on_hand - Inventory.qty_reserved) >= qty,
        )
        .values(qty_on_hand=Inventory.qty_on_hand - qty)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        available = db.scalar(
            select(Inventory.qty_on_hand - Inventory.qty_reserved).where(
                Inventory.variant_id == variant_id
            )
        )
        raise OutOfStock(
            "库存不足",
            variant_id=variant_id,
            requested_qty=qty,
            available_qty=max(0, available or 0),
        )


def deduct_locked(db: Session, lines: list[StockLine]) -> None:
    """多行扣减，全成或全败。

    任何一行不够就抛异常，由调用方回滚整个事务。
    部分成功比失败更糟 —— 客户会拿到一张只发一半货的订单。
    """
    lock_variants(db, [line.variant_id for line in lines])
    for line in lines:
        deduct_one(db, line.variant_id, line.qty)

    db.flush()  # 让 CHECK 约束在此刻触发，而不是拖到 commit
    # 上面的 UPDATE 走的是 Core 层，绕过了 ORM 的 identity map。
    # 不 expire 的话，后续读到的是内存里的旧库存。
    db.expire_all()


def restock_locked(db: Session, lines: list[StockLine]) -> None:
    """取消订单时回补库存。走同一套加锁路径。

    忘记回补是这类系统常见的数据腐坏源：库存会凭空减少，
    而且没有任何报错，直到某天发现所有商品都显示缺货。
    """
    lock_variants(db, [line.variant_id for line in lines])
    for line in lines:
        db.execute(
            update(Inventory)
            .where(Inventory.variant_id == line.variant_id)
            .values(qty_on_hand=Inventory.qty_on_hand + line.qty)
            .execution_options(synchronize_session=False)
        )
    db.expire_all()


def snapshot_stock(db: Session, variant_id: int) -> int:
    """不加锁地读当前可售数量。用于报价阶段的软检查。"""
    value = db.scalar(
        select(Inventory.qty_on_hand - Inventory.qty_reserved).where(
            Inventory.variant_id == variant_id
        )
    )
    return max(0, value or 0)
