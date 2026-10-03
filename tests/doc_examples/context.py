"""What the examples in the docs take as given.

The models, views and helpers here are the ones the docs name without defining,
so each example is checked against a model that has the columns it uses. They
are only type-checked, never run.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import JSON, Column, ForeignKey, Numeric, String, Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.requests import Request
from starlette.responses import Response

from adminsite import (
    Admin,
    AdminPage,
    BaseField,
    Chart,
    ColumnReference,
    DeleteContext,
    Descending,
    Field,
    FieldsetWidget,
    Html,
    Inline,
    Link,
    Message,
    ModelView,
    PanelWidget,
    Permission,
    Plugin,
    RecentRecords,
    RefusedError,
    RequestAction,
    RowWidget,
    SaveContext,
    Stat,
    Statement,
    TabsWidget,
    Widget,
)
from adminsite.actions import Input, Selection, action
from adminsite.auth import AuthProvider, PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import SessionAdapter
from adminsite.fields import (
    ComputedField,
    DecimalField,
    EnumField,
    FileField,
    IntegerField,
    JSONField,
    ListField,
    PasswordField,
    RelationField,
    StringField,
    TextAreaField,
)
from adminsite.files import LocalStorage

__all__ = [
    "JSON",
    "STATUSES",
    "Account",
    "Admin",
    "AdminPage",
    "ApiKey",
    "AsyncSession",
    "AuthProvider",
    "Base",
    "BaseField",
    "BaseModel",
    "BuyerView",
    "ChangeLog",
    "Chart",
    "Check",
    "Column",
    "ColumnReference",
    "ComputedField",
    "Config",
    "Contact",
    "Country",
    "Customer",
    "CustomerGroup",
    "CustomerView",
    "DecimalField",
    "DeleteContext",
    "Delivery",
    "Descending",
    "EnumField",
    "Event",
    "Field",
    "FieldsetWidget",
    "FileField",
    "ForeignKey",
    "Group",
    "GroupSetting",
    "Html",
    "Inline",
    "Input",
    "IntegerField",
    "Invoice",
    "JSONField",
    "Link",
    "ListField",
    "LocalStorage",
    "Maintenance",
    "Mapped",
    "Member",
    "Message",
    "ModelView",
    "Notification",
    "Numeric",
    "Order",
    "OrderItem",
    "OrderStatus",
    "OrderView",
    "PanelWidget",
    "PasswordAuth",
    "PasswordField",
    "Payment",
    "Percentage",
    "Permission",
    "Plugin",
    "Product",
    "ProductView",
    "Proxy",
    "RecentRecords",
    "RefusedError",
    "RelationField",
    "ReportSchedule",
    "ReportScheduleView",
    "Request",
    "RequestAction",
    "Response",
    "RowWidget",
    "Run",
    "Salary",
    "SalesReport",
    "SaveContext",
    "Selection",
    "SessionAdapter",
    "Setting",
    "Shop",
    "ShopSettings",
    "Stat",
    "Statement",
    "StringField",
    "Supplier",
    "Table",
    "TabsWidget",
    "Tag",
    "TextAreaField",
    "User",
    "Wallet",
    "Widget",
    "action",
    "admin",
    "app",
    "auth",
    "engine",
    "export_sales",
    "fetch_new_orders",
    "fetch_weather",
    "func",
    "hash_password",
    "issue_key",
    "last_monday",
    "last_month_start",
    "mapped_column",
    "month_start",
    "note_attempt",
    "queue_export",
    "relationship",
    "render_csv",
    "save_settings",
    "select",
    "send_confirmation",
    "session_factory",
    "settings",
    "slugify",
    "staff_auth",
    "support_auth",
    "too_many_lately",
    "warehouses_for",
]


class Base(DeclarativeBase):
    """The application's models."""


class OrderStatus(enum.StrEnum):
    """Where an order is."""

    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    FAILED = "failed"
    REFUNDED = "refunded"


class Country(enum.StrEnum):
    """Where an order goes."""

    DE = "DE"
    NL = "NL"
    UK = "UK"


class Customer(Base):
    """Someone who buys from the shop."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    region: Mapped[str] = mapped_column(String(2), default="EU")
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(default=None)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Supplier(Base):
    """Someone the shop buys from."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    closed_at: Mapped[datetime | None] = mapped_column(default=None)


class Tag(Base):
    """A word a product is found by."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))


product_tags = Table(
    "product_tags",
    Base.metadata,
    Column("product_id", ForeignKey("products.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class Product(Base):
    """Something the shop sells."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    size: Mapped[str] = mapped_column(String(1), default="M")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    limit: Mapped[int] = mapped_column(default=10)
    description: Mapped[str | None] = mapped_column(default=None)
    photo: Mapped[str | None] = mapped_column(String(255), default=None)
    datasheet: Mapped[str | None] = mapped_column(String(255), default=None)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    sizes: Mapped[list[int]] = mapped_column(JSON, default=list)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))

    supplier: Mapped[Supplier] = relationship()
    tags: Mapped[list[Tag]] = relationship(secondary=product_tags)
    slots: Mapped[list["Run"]] = relationship(back_populates="product")


class Order(Base):
    """What a customer bought, and where it is."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.PENDING)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    region: Mapped[str] = mapped_column(String(2), default="EU")
    country: Mapped[Country] = mapped_column(default=Country.DE)
    high_risk: Mapped[bool] = mapped_column(default=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    carrier: Mapped[str | None] = mapped_column(String(20), default=None)
    tracking: Mapped[str | None] = mapped_column(String(40), default=None)
    tracking_url: Mapped[str | None] = mapped_column(String(255), default=None)
    due_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(Base):
    """One line of an order."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Run(Base):
    """A batch of a product, made or bought in one go."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    product: Mapped[Product] = relationship(back_populates="slots")


class Notification(Base):
    """A message waiting to go out about an order."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))


class User(Base):
    """Someone who signs in."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_staff: Mapped[bool] = mapped_column(default=False)
    signed_up_at: Mapped[datetime] = mapped_column(server_default=func.now())

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="user")


class Invoice(Base):
    """What a user owes."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    user: Mapped[User] = relationship(back_populates="invoices")


class Payment(Base):
    """Money paid against an invoice."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))


class ApiKey(Base):
    """A key a script signs in with."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(default=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    user: Mapped[User] = relationship()


class Account(Base):
    """A customer's account with a partner, and its key."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Wallet(Base):
    """A balance a customer keeps with the shop."""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    balance: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
    seed_ciphertext: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))

    owner: Mapped[Customer] = relationship()


class Salary(Base):
    """What someone is paid."""

    __tablename__ = "salaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Contact(Base):
    """Someone to call."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), index=True)


class Event(Base):
    """Something that happened, on a table of millions."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Group(Base):
    """A group of members."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Member(Base):
    """Someone in a group."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))


class GroupSetting(Base):
    """One setting of a group, kept as a row."""

    __tablename__ = "group_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    name: Mapped[str] = mapped_column(String(40))
    value: Mapped[Any] = mapped_column(JSON)


class Setting(Base):
    """A setting of the shop, one row each."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Shop(Base):
    """A shop and its settings."""

    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CustomerGroup(Base):
    """Customers who get settings of their own."""

    __tablename__ = "customer_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Proxy(Base):
    """A server to send requests through."""

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(255))


class Config(Base):
    """A configuration, with the proxies it tries in turn."""

    __tablename__ = "configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))

    proxies: Mapped[list[Proxy]] = relationship(secondary="config_proxies")


class Check(Base):
    """A check that runs on a schedule."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_delay: Mapped[int] = mapped_column(default=60)
    validation_delay: Mapped[int | None] = mapped_column(default=None)


class ChangeLog(Base):
    """The application's own log of changes."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime]
    table_name: Mapped[str]
    row_key: Mapped[str]
    event: Mapped[str]
    changes: Mapped[dict[str, list[Any]]] = mapped_column(JSON)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None]
    who: Mapped[str | None]
    who_name: Mapped[str | None]
    ip: Mapped[str | None]
    user_agent: Mapped[str | None]


class ReportSchedule(Base):
    """When a report is sent, and to whom."""

    __tablename__ = "report_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))


class Delivery(BaseModel):
    """The delivery setting."""

    free_over: Decimal = Decimal(50)


class Maintenance(BaseModel):
    """The maintenance setting."""

    active: bool = False
    message: str = ""


class ShopSettings(BaseModel):
    """The shop's own settings."""

    open: bool = True
    free_shipping_over: float = 50


class Percentage(float):
    """A share of a hundred, as a column of its own type holds it."""


class OrderView(ModelView[Order]):
    """The orders."""

    fields = [Order.id, Order.customer, Order.status, Order.total, Order.created_at]


class CustomerView(ModelView[Customer]):
    """The customers."""

    fields = [Customer.name, Customer.email, Customer.region]


class ProductView(ModelView[Product]):
    """The products."""

    fields = [Product.name, Product.price]


class BuyerView(ModelView[Customer]):
    """The customers who have bought something."""

    name = "buyers"


class ReportScheduleView(ModelView[ReportSchedule]):
    """When the reports go out."""


class SalesReport(AdminPage):
    """What the shop sold."""

    label = "Sales report"
    template = "reports/sales.html"


@dataclass(frozen=True)
class Settings:
    """The application's settings, read from its environment."""

    admin_secret_key: str = ""
    admin_password_hash: str = ""
    staff_secret: str = ""
    support_secret: str = ""


settings = Settings()
engine = create_async_engine("postgresql+asyncpg://localhost/shop")
session_factory = async_sessionmaker(engine)
app = FastAPI()
auth = PasswordAuth({"nima": settings.admin_password_hash})
staff_auth = PasswordAuth({"nima": settings.admin_password_hash})
support_auth = PasswordAuth({"lena": settings.admin_password_hash})
admin = Admin(engine, views=[OrderView, CustomerView])

last_monday = datetime(2026, 9, 21)
month_start = datetime(2026, 9, 1)
last_month_start = datetime(2026, 8, 1)

STATUSES = [
    ("pending", "در انتظار"),
    ("paid", "پرداخت شده"),
    ("shipped", "ارسال شده"),
]


def slugify(text: str) -> str:
    """The text as it goes in an address."""
    return "-".join(text.lower().split())


def render_csv(order: Order) -> str:
    """The order's lines as CSV."""
    return "product,quantity\n"


def warehouses_for(user: Any) -> list[tuple[str, str]]:
    """The warehouses this user may move stock to."""
    return [("rotterdam", "Rotterdam")]


async def send_confirmation(order: Order) -> None:
    """Email the customer that the order is in."""


async def fetch_new_orders(session: SessionAdapter) -> int:
    """Fetch the orders the provider has, and say how many were new."""
    return 0


async def issue_key(session: SessionAdapter, account: Account) -> str:
    """Make a new key for the account, and forget the old one."""
    return ""


async def queue_export(session: SessionAdapter) -> None:
    """Start an export that finishes later."""


async def note_attempt(username: str, address: str) -> None:
    """Write down a failed sign in."""


async def too_many_lately(address: str) -> bool:
    """Whether this address failed to sign in too often lately."""
    return False


async def fetch_weather(city: str) -> dict[str, float]:
    """The weather in the city."""
    return {"temperature": 18.0}


async def save_settings(form: Mapping[str, Any]) -> None:
    """Store the settings the form holds."""


async def export_sales(admin: Admin, request: Request) -> Response:
    """The sales, as a CSV file."""
    return Response("", media_type="text/csv")
