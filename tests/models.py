import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    REFUNDED = "refunded"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    region: Mapped[str] = mapped_column(String(2), default="EU")
    is_active: Mapped[bool] = mapped_column(default=True)

    orders: Mapped[list["Order"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
    )

    def __str__(self) -> str:
        return self.name


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    description: Mapped[str | None] = mapped_column(String(500), default=None)

    def __str__(self) -> str:
        return self.name


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.PENDING)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )

    def __str__(self) -> str:
        return f"Order #{self.id}"


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Shelf(Base):
    """A row whose key is two columns, for what has to handle composite keys."""

    __tablename__ = "shelves"

    aisle: Mapped[str] = mapped_column(String(2), primary_key=True)
    slot: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(60))

    def __str__(self) -> str:
        return f"{self.aisle}{self.slot}"


class Account(Base):
    """A row keeping a password as its hash, for inputs that are not columns."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    def __str__(self) -> str:
        return self.email


class Setting(Base):
    """A row with JSON in it, for the fields that read and write documents."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    def __str__(self) -> str:
        return self.name


# An article's tags, in the order they were given: the link rows have a
# serial id, and the relationship reads them by it.
article_tags = Table(
    "article_tags",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("article_id", ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
)


class Tag(Base):
    """A small table that a link to many records points at."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))

    def __str__(self) -> str:
        return self.name


class Article(Base):
    """Links to many tags, kept in the order they were given."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    tags: Mapped[list[Tag]] = relationship(
        secondary=article_tags, order_by=article_tags.c.id
    )

    def __str__(self) -> str:
        return self.title
