"""测试夹具。

★ 环境变量必须在 import app.* 之前设好 —— app.core.config 在导入时就会
  实例化 Settings，晚一步设置就晚了。
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="wa-sales-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["LLM_MODE"] = "mock"
os.environ["OPENAI_API_KEY"] = ""

import pytest  # noqa: E402

from app.core.database import Base, engine, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    AttributeAlias,
    Customer,
    CustomerTier,
    Inventory,
    Product,
    ProductVariant,
)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    """一个干净的会话。测试之间互不干扰。"""
    with session_scope() as session:
        yield session


@pytest.fixture(autouse=True)
def _clean_tables():
    from app.services.maintenance import wipe_business_data

    with session_scope() as session:
        wipe_business_data(session)
    yield


@pytest.fixture
def tier_retail():
    with session_scope() as session:
        session.add(CustomerTier(code="retail", name="零售", default_discount_pct=0))
        session.flush()
    with session_scope() as session:
        return session.query(CustomerTier).filter_by(code="retail").one()


@pytest.fixture
def make_variant():
    """建一个带库存的变体。

    默认库存 1 —— 防超卖测试就靠这个数字，
    「10 个人抢最后 1 件」是最容易暴露竞态的场景。
    """

    def _make(*, sku="TEST-001", stock=1, price_cents=19900, floor_cents=15900,
              color="black", size="XL"):
        with session_scope() as session:
            product = Product(sku=sku, name=f"{sku} 测试商品", is_active=True)
            session.add(product)
            session.flush()
            variant = ProductVariant(
                product_id=product.id,
                variant_sku=f"{sku}-{color}-{size}",
                color_code=color, color_label=color,
                size_code=size, size_label=size,
                list_price_cents=price_cents,
                floor_price_cents=floor_cents,
                is_active=True,
            )
            session.add(variant)
            session.flush()
            session.add(Inventory(variant_id=variant.id, qty_on_hand=stock, qty_reserved=0))
            return variant.id

    return _make


@pytest.fixture
def make_customer():
    def _make(*, phone="8613800138000", tier_code="retail"):
        with session_scope() as session:
            tier = session.query(CustomerTier).filter_by(code=tier_code).first()
            if tier is None:
                tier = CustomerTier(code=tier_code, name=tier_code, default_discount_pct=0)
                session.add(tier)
                session.flush()
            customer = Customer(phone=phone, name="测试客户", tier_id=tier.id)
            session.add(customer)
            session.flush()
            return customer.id

    return _make
