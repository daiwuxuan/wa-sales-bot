"""订单管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.money import to_yuan
from app.models import Order, utcnow
from app.services import inventory as inventory_service
from app.services.orders import order_summary_for_admin

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("")
def list_orders(
    db: Session = Depends(get_db),
    status: str | None = None,
    phone: str | None = None,
    limit: int = Query(default=100, le=500),
) -> dict:
    stmt = (
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.customer))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Order.status == status)
    if phone:
        stmt = stmt.where(Order.customer_phone == phone)

    return {"items": [order_summary_for_admin(o) for o in db.scalars(stmt)]}


@router.get("/{order_no}")
def get_order(order_no: str, db: Session = Depends(get_db)) -> dict:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.customer))
        .where(Order.order_no == order_no)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")

    payload = order_summary_for_admin(order)
    payload["idempotency_key"] = order.idempotency_key
    payload["quote_id"] = order.quote_id
    payload["conversation_id"] = order.conversation_id
    payload["subtotal_display"] = f"¥{to_yuan(order.subtotal_cents):.2f}"
    payload["discount_display"] = f"¥{to_yuan(order.discount_total_cents):.2f}"
    payload["shipping_display"] = f"¥{to_yuan(order.shipping_cents):.2f}"
    return payload


@router.post("/{order_no}/cancel")
def cancel_order(order_no: str, db: Session = Depends(get_db)) -> dict:
    """取消订单并回补库存。

    ★ 必须回补。忘记回补是这类系统最常见的数据腐坏源 ——
      库存凭空减少，没有任何报错，直到某天发现所有商品都显示缺货。
    """
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_no == order_no)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status == "cancelled":
        return {"ok": True, "order_no": order_no, "status": "cancelled", "note": "已经是取消状态"}

    inventory_service.restock_locked(
        db,
        [inventory_service.StockLine(i.variant_id, i.qty) for i in order.items],
    )
    order.status = "cancelled"
    order.cancelled_at = utcnow()
    db.commit()

    return {"ok": True, "order_no": order_no, "status": "cancelled", "restocked": len(order.items)}


@router.post("/{order_no}/status")
def update_status(order_no: str, status: str, db: Session = Depends(get_db)) -> dict:
    allowed = {"pending_confirm", "confirmed", "paid", "shipped", "completed", "cancelled"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"状态必须是 {sorted(allowed)} 之一")

    order = db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")

    order.status = status
    db.commit()
    return {"ok": True, "order_no": order_no, "status": status}
