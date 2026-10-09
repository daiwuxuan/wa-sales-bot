"""商品、变体、库存、属性别名。

建模要点：SKU 分两层。
  products.sku          —— 款号，如 TSHIRT-001（SPU 语义）
  product_variants      —— 款下的颜色×尺码组合，价格挂在这一层
  inventory             —— 库存挂在这一层，一变体一行

为什么颜色/尺码不能只存在 inventory 里：价格和库存都可能因颜色尺码而异，
订单行必须指向具体 variant 才能精确扣减；而且将来要给变体加重量、
条码之类的字段时，塞在 inventory 里就无处安放了。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    brand: Mapped[str | None] = mapped_column(String(64))

    # 商家在后台自己上传的商品主图（不是 AI 生成的）。
    # 存根相对路径，如 "/uploads/ab12cd.jpg"。
    # 绝不存完整 URL —— 换域名或换 CDN 时要改的是配置，不是每一行数据。
    image_url: Mapped[str | None] = mapped_column(String(500))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )


class ProductVariant(Base, TimestampMixin):
    __tablename__ = "product_variants"
    __table_args__ = (
        # search_inventory 的主查询索引：按款 + 颜色 + 尺码定位
        UniqueConstraint("product_id", "color_code", "size_code", name="uq_variant_combo"),
        CheckConstraint("list_price_cents >= 0", name="ck_variant_price_nonneg"),
        CheckConstraint("floor_price_cents >= 0", name="ck_variant_floor_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )

    # 供人工和后台使用的变体号，如 TSHIRT-001-BLK-XL
    variant_sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)

    # 规范码（英文小写）用于匹配，label 用于展示。
    # 客户说「黑色」，DB 存 black，中间靠 attribute_aliases 归一化。
    color_code: Mapped[str] = mapped_column(String(32))
    color_label: Mapped[str] = mapped_column(String(64))
    size_code: Mapped[str] = mapped_column(String(32))
    size_label: Mapped[str] = mapped_column(String(64))

    # 当前售价。历史价格见 variant_prices（二期），一期直接覆盖。
    list_price_cents: Mapped[int] = mapped_column(Integer)

    # ★ 最低价。这个数字永远不会进入模型上下文 —— 见 ai/prompts.py
    floor_price_cents: Mapped[int] = mapped_column(Integer)

    weight_grams: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    product: Mapped[Product] = relationship(back_populates="variants")
    inventory: Mapped[Inventory | None] = relationship(
        back_populates="variant", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class Inventory(Base, TimestampMixin):
    """库存。一变体一行。"""

    __tablename__ = "inventory"
    __table_args__ = (
        # ★ 最后一道防线：即使应用层有 bug，也不可能写出负库存。
        #   SQLite 和 Postgres 都支持 CHECK。
        CheckConstraint("qty_on_hand >= 0", name="ck_inventory_on_hand_nonneg"),
        CheckConstraint("qty_reserved >= 0", name="ck_inventory_reserved_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), unique=True, index=True
    )

    qty_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    qty_reserved: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=5)

    variant: Mapped[ProductVariant] = relationship(back_populates="inventory")

    @property
    def available_qty(self) -> int:
        return self.qty_on_hand - self.qty_reserved


class AttributeAlias(Base, TimestampMixin):
    """属性别名 -> 规范码。

    这张表是必需的，不是可选项。客户永远说中文（「黑色」「加大」「L码」），
    DB 里存的是 black / XL。没有这一层归一化，search_inventory 会大面积查空，
    主链路第一步就断。
    """

    __tablename__ = "attribute_aliases"
    __table_args__ = (
        UniqueConstraint("attr_type", "alias", name="uq_alias"),
        Index("ix_alias_lookup", "attr_type", "alias"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attr_type: Mapped[str] = mapped_column(String(16))  # color | size
    alias: Mapped[str] = mapped_column(String(64))      # 归一化后的输入（小写去空格）
    canonical: Mapped[str] = mapped_column(String(32))  # black / XL
    label: Mapped[str | None] = mapped_column(String(64))
