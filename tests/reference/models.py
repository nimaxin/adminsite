"""The reference shop: the example shop's models, with what the larger views need."""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class ReferenceBase(DeclarativeBase):
    """Kept apart from the test models, so nothing here reaches their tables."""


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    REFUNDED = "refunded"


class ProductStatus(enum.StrEnum):
    DRAFT = "draft"
    LIVE = "live"
    RETIRED = "retired"


class PromotionKind(enum.StrEnum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class Customer(ReferenceBase):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    region: Mapped[str] = mapped_column(String(2), default="EU")
    is_active: Mapped[bool] = mapped_column(default=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Supplier(ReferenceBase):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    country: Mapped[str] = mapped_column(String(2))

    products: Mapped[list["Product"]] = relationship(back_populates="supplier")


class Tag(ReferenceBase):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))


product_tags = Table(
    "product_tags",
    ReferenceBase.metadata,
    Column(
        "product_id", ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Product(ReferenceBase):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    status: Mapped[ProductStatus] = mapped_column(default=ProductStatus.DRAFT)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(default=0)
    reorder_level: Mapped[int] = mapped_column(default=5)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    photo: Mapped[str | None] = mapped_column(String(255), default=None)
    dimensions: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    supplier: Mapped[Supplier] = relationship(back_populates="products")
    tags: Mapped[list[Tag]] = relationship(secondary=product_tags)


class Order(ReferenceBase):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.PENDING)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(ReferenceBase):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


promotion_tags = Table(
    "promotion_tags",
    ReferenceBase.metadata,
    Column(
        "promotion_id",
        ForeignKey("promotions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Promotion(ReferenceBase):
    """A shop promotion, with a column of every kind the field gallery shows."""

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    terms: Mapped[str | None] = mapped_column(Text, default=None)
    uses_left: Mapped[int] = mapped_column(default=100)
    discount_rate: Mapped[float] = mapped_column(default=0.1)
    minimum_spend: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    active: Mapped[bool] = mapped_column(default=True)
    starts_on: Mapped[date | None] = mapped_column(default=None)
    ends_at: Mapped[datetime | None] = mapped_column(default=None)
    daily_start: Mapped[time | None] = mapped_column(default=None)
    kind: Mapped[PromotionKind] = mapped_column(default=PromotionKind.PERCENTAGE)
    audience: Mapped[str] = mapped_column(String(1), default="A")
    contact_email: Mapped[str | None] = mapped_column(String(255), default=None)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    terms_file: Mapped[str | None] = mapped_column(String(255), default=None)
    banner: Mapped[str | None] = mapped_column(String(255), default=None)
    secret_code_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    sponsor_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id"), default=None
    )

    sponsor: Mapped[Supplier | None] = relationship()
    tags: Mapped[list[Tag]] = relationship(secondary=promotion_tags)
