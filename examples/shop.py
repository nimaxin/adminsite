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
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.responses import Response

from adminsite import (
    Admin,
    Chart,
    Inline,
    ModelView,
    Permission,
    RecentRecords,
    Stat,
)
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import SessionAdapter
from adminsite.fields import ChoiceField, ImageField, RelationField
from adminsite.files import LocalStorage


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
    photo: Mapped[str | None] = mapped_column(String(255), default=None)

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
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
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


def outline(paths: str) -> str:
    """A sidebar icon drawn the way the admin draws its own."""
    return (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )


class CustomerView(ModelView, model=Customer):
    group = "Sales"
    icon = outline(
        '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
        '<path d="M16 4.5a3.5 3.5 0 0 1 0 7"/>'
        '<path d="M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>'
    )
    display_template = "{name} ({email})"
    list_display = ("name", "email", "region", "is_active")
    search_fields = ("name", "email")
    list_filter = ("region", "is_active")
    ordering = ("name",)
    can_import = True


class OrderView(ModelView, model=Order):
    group = "Sales"
    icon = outline(
        '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 8h6M9 12h6"/>'
    )
    display_template = "Order #{id}"
    list_display = ("id", "customer.name", "status", "total", "created_at")
    list_columns = ("customer.email", "note")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at")
    ordering = ("-created_at",)
    readonly_fields = ("total",)
    inlines = (Inline("items", fields=("product", "quantity", "unit_price")),)
    fields = (
        RelationField("customer", target=Customer, display_template="{name} ({email})"),
    )

    @action("Mark as paid", on="record")
    async def mark_paid(self, record: Order, session: SessionAdapter) -> str:
        record.status = OrderStatus.PAID
        return f"Order #{record.id} marked as paid."

    @action("Download as CSV", on="record", permission=Permission.EXPORT)
    async def download(self, record: Order, session: SessionAdapter) -> Response:
        lines = "\n".join(
            f"{item.product.name},{item.quantity},{item.unit_price}"
            for item in record.items
        )
        return Response(
            f"product,quantity,price\n{lines}\n",
            media_type="text/csv",
            headers={
                "content-disposition": f'attachment; filename="order-{record.id}.csv"'
            },
        )

    @action("Today's takings", on="view", permission=Permission.VIEW)
    async def takings(self, session: SessionAdapter) -> str:
        today = datetime.now(UTC).replace(tzinfo=None).date()
        total = await session.scalar(
            select(func.sum(Order.total)).where(func.date(Order.created_at) == today)
        )
        return f"Today's orders come to €{total or 0}."

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
    icon = outline(
        '<path d="m3 7.5 9-4.5 9 4.5v9L12 21l-9-4.5z"/><path d="m3 7.5 9 4.5 9-4.5"/>'
        '<path d="M12 12v9"/>'
    )
    list_display = ("name", "photo", "price", "description")
    fields = (ImageField("photo", storage=LocalStorage("shop_uploads")),)
    search_fields = ("name", "description")
    list_filter = ("price",)


engine = create_async_engine("sqlite+aiosqlite:///shop.db")


order_day = func.date(Order.created_at)

dashboard = [
    Stat("Revenue", select(func.sum(Order.total)), format="€{:,.2f}"),
    Stat("Orders", select(func.count(Order.id)), link="orders"),
    Stat(
        "Waiting to ship",
        select(func.count()).where(Order.status == OrderStatus.PAID),
        link="orders?status=PAID",
    ),
    Stat("Customers", select(func.count(Customer.id)), link="customers"),
    Chart(
        "Revenue per day",
        select(order_day, func.sum(Order.total))
        .group_by(order_day)
        .order_by(order_day),
        format="€{:,.2f}",
    ),
    RecentRecords("Latest orders", "orders", sort="-created_at", detail="total"),
]


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
    dashboard=dashboard,
    # Hash the password where you keep it, not here.
    auth=PasswordAuth({"nima": hash_password("letmein")}),
    secret_key="change-this-before-you-deploy-anything",
    # Every change goes to adminsite_audit.db, shown on each record's
    # History tab and on the Activity page.
    audit=True,
    # Saved views go to adminsite_views.db.
    saved_views=True,
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

    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    statuses = list(OrderStatus)
    orders = []
    for index in range(24):
        items = [
            OrderItem(
                product=products[(index + line) % len(products)],
                quantity=1 + (index + line) % 3,
                unit_price=products[(index + line) % len(products)].price,
            )
            for line in range(1 + index % 3)
        ]
        orders.append(
            Order(
                customer=customers[index % len(customers)],
                status=statuses[index % len(statuses)],
                # The total is what the lines add up to, so an order's page
                # never contradicts itself.
                total=sum(
                    (item.unit_price * item.quantity for item in items), Decimal(0)
                ),
                created_at=now - timedelta(days=index, minutes=(index * 97) % 600),
                items=items,
            )
        )
    return [*customers, *products, *orders]
