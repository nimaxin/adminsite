"""A small shop with an admin, for trying adminsite out.

    uv run uvicorn examples.shop:app --reload

Then open http://127.0.0.1:8000/admin and sign in as nima / letmein.
"""

import enum
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy import DateTime, ForeignKey, Numeric, String, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from adminsite import Admin, ModelView
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.fields import ChoiceField, RelationField


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

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")

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
    created_at: Mapped[datetime] = mapped_column(DateTime)

    customer: Mapped[Customer] = relationship(back_populates="orders")

    def __str__(self) -> str:
        return f"Order #{self.id}"


class CustomerView(ModelView, model=Customer):
    group = "Sales"
    display_template = "{name} ({email})"
    list_display = ("name", "email", "region", "is_active")
    search_fields = ("name", "email")
    list_filter = ("region", "is_active")
    ordering = ("name",)


class OrderView(ModelView, model=Order):
    group = "Sales"
    display_template = "Order #{id}"
    list_display = ("id", "customer.name", "status", "total", "created_at")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at")
    ordering = ("-created_at",)
    readonly_fields = ("total",)
    fields = (
        RelationField("customer", target=Customer, display_template="{name} ({email})"),
    )

    @action(
        "Mark as shipped",
        confirm="Mark the chosen orders as shipped?",
        inputs=[
            ChoiceField(
                "carrier",
                choices=(("dhl", "DHL Express"), ("ups", "UPS"), ("postnl", "PostNL")),
                required=True,
            ),
        ],
    )
    async def ship(self, selection: Selection, carrier: str) -> str:
        changed = await selection.update(
            status=OrderStatus.SHIPPED, note=f"Sent with {carrier.upper()}"
        )
        return f"{changed} orders marked as shipped with {carrier.upper()}."


class ProductView(ModelView, model=Product):
    group = "Catalogue"
    list_display = ("name", "price", "description")
    search_fields = ("name", "description")
    list_filter = ("price",)


engine = create_async_engine("sqlite+aiosqlite:///shop.db")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and put some records in, the first time."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        if not await session.scalar(select(Customer.id).limit(1)):
            session.add_all(build_sample_shop())
            await session.commit()
    yield


app = FastAPI(title="Acme shop", lifespan=lifespan)

admin = Admin(
    engine,
    title="Acme shop",
    views=[OrderView, CustomerView, ProductView],
    # Hash the password where you keep it, not here.
    auth=PasswordAuth({"nima": hash_password("letmein")}),
    secret_key="change-this-before-you-deploy-anything",
)
app.mount("/admin", admin)


def build_sample_shop() -> list[Base]:
    """A handful of customers, products and orders to click through."""
    customers = [
        Customer(name="Lena Fischer", email="lena@fischer.de", region="DE"),
        Customer(name="Marco Rossi", email="marco@rossi.it", region="IT"),
        Customer(name="Aisha Khan", email="aisha@khan.co.uk", region="UK"),
        Customer(name="Jonas Berg", email="jonas@berg.se", region="SE"),
    ]
    products = [
        Product(name="Linen shirt", price=Decimal("59.00")),
        Product(name="Canvas tote", price=Decimal("24.00")),
        Product(name="Wool scarf", price=Decimal("38.50")),
    ]

    now = datetime.now(UTC).replace(tzinfo=None)
    statuses = list(OrderStatus)
    orders = [
        Order(
            customer=customers[index % len(customers)],
            status=statuses[index % len(statuses)],
            total=Decimal(20 + index * 7),
            created_at=now - timedelta(days=index),
        )
        for index in range(24)
    ]
    return [*customers, *products, *orders]
