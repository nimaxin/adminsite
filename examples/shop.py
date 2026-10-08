"""A small shop with an admin, for trying adminsite out.

    uv run uvicorn examples.shop:app --reload

Then open http://127.0.0.1:8000/admin and sign in as nima / letmein.
"""

import enum
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Table,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.responses import Response

from adminsite import (
    Admin,
    Chart,
    Descending,
    Field,
    Inline,
    Link,
    ModelView,
    Permission,
    RecentRecords,
    Stat,
)
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import SessionAdapter
from adminsite.fields import EnumField, ImageField
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


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))

    def __str__(self) -> str:
        return self.name


product_tags = Table(
    "product_tags",
    Base.metadata,
    Column(
        "product_id", ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    photo: Mapped[str | None] = mapped_column(String(255), default=None)

    tags: Mapped[list[Tag]] = relationship(secondary=product_tags)

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


# How the shop writes an amount of money, on every page and in the export.
EUROS = "€{:,.2f}"


def outline(paths: str) -> str:
    """An icon for the sidebar or an action, drawn the way the admin draws its own."""
    return (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )


class CustomerView(ModelView[Customer]):
    group = "Sales"
    icon = outline(
        '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
        '<path d="M16 4.5a3.5 3.5 0 0 1 0 7"/>'
        '<path d="M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>'
    )
    record_title = "{name} ({email})"
    fields = [Customer.name, Customer.email, Customer.region, Customer.is_active]
    searchable_fields = [Customer.name, Customer.email]
    list_filters = [Customer.region, Customer.is_active]
    fields_default_sort = [Customer.name]
    can_import = True


class OrderView(ModelView[Order]):
    group = "Sales"
    icon = outline(
        '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 8h6M9 12h6"/>'
    )
    record_title = "Order #{id}"
    fields = [
        Order.id,
        # The list names the customer, the form offers a picker.
        Order.customer,
        # One column of the customer, shown and never edited.
        Field(Link(Order.customer, Customer.email), hidden_in_list=True),
        EnumField(
            Order.status,
            tones={
                OrderStatus.PENDING: "amber",
                OrderStatus.PAID: "blue",
                OrderStatus.SHIPPED: "green",
                OrderStatus.REFUNDED: "rose",
            },
        ),
        Field(Order.total, format=EUROS, read_only=True),
        Field(Order.note, hidden_in_list=True),
        Order.created_at,
    ]
    # A status moves along often, so it changes straight from the list, and
    # so does the note, once the Columns menu shows it.
    inline_editable_fields = [Order.status, Order.note]
    searchable_fields = [
        Order.id,
        Link(Order.customer, Customer.name),
        Link(Order.customer, Customer.email),
    ]
    list_filters = [Order.status, Order.total, Order.created_at]
    fields_default_sort = [Descending(Order.created_at)]
    inlines = [
        Inline(
            Order.items,
            fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
        )
    ]

    @action(
        "Mark as paid",
        on="record",
        icon=outline(
            '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>'
        ),
    )
    async def mark_paid(self, record: Order, session: SessionAdapter) -> str:
        record.status = OrderStatus.PAID
        return f"Order #{record.id} marked as paid."

    @action(
        "Download as CSV",
        on="record",
        permission=Permission.EXPORT,
        icon=outline(
            '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>'
        ),
    )
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
        icon=outline(
            '<path d="M2 6h12v10H2z"/><path d="M14 10h4l4 3v3h-8"/>'
            '<circle cx="6.5" cy="17.5" r="1.8"/><circle cx="17.5" cy="17.5" r="1.8"/>'
        ),
    )
    async def ship(
        self,
        selection: Selection[Order],
        *,
        # Asked for in the dialog, as a select of these three.
        carrier: Literal["DHL", "UPS", "PostNL"],
    ) -> str:
        changed = await selection.update(
            status=OrderStatus.SHIPPED, note=f"Sent with {carrier}"
        )
        return f"{changed} orders marked as shipped with {carrier}."


class ProductView(ModelView[Product]):
    group = "Catalogue"
    icon = outline(
        '<path d="m3 7.5 9-4.5 9 4.5v9L12 21l-9-4.5z"/><path d="m3 7.5 9 4.5 9-4.5"/>'
        '<path d="M12 12v9"/>'
    )
    fields = [
        Product.name,
        ImageField(Product.photo, storage=LocalStorage("shop_uploads")),
        Field(Product.price, format=EUROS),
        Product.tags,
        Product.description,
    ]
    searchable_fields = [Product.name, Product.description]
    list_filters = [Product.price]


class TagView(ModelView[Tag]):
    group = "Catalogue"
    icon = outline(
        '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/>'
        '<circle cx="7.5" cy="7.5" r="1.5"/>'
    )
    record_title = "{name}"
    searchable_fields = [Tag.name]
    fields_default_sort = [Tag.name]


engine = create_async_engine("sqlite+aiosqlite:///shop.db")


order_day = func.date(Order.created_at)

dashboard = [
    Stat("Revenue", select(func.sum(Order.total)), format=EUROS),
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
        format=EUROS,
    ),
    RecentRecords("Latest orders", "orders", sort="-created_at", value="total"),
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
    views=[OrderView, CustomerView, ProductView, TagView],
    dashboard=dashboard,
    # Hash the password where you keep it, not here.
    auth=PasswordAuth({"nima": hash_password("letmein")}),
    secret_key="change-this-before-you-deploy-anything",
    # Every change goes to adminsite_audit.db, shown on each record's
    # History tab and on the Activity page.
    audit=True,
    # Saved views go to adminsite_views.db.
    saved_views=True,
    # Times follow each browser's time zone; this menu shows them in another.
    timezones=["Europe/Paris", "Asia/Tehran", "America/New_York"],
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
    tags = {
        name: Tag(name=name)
        for name in (
            "New",
            "Bestseller",
            "Organic",
            "Handmade",
            "Gift idea",
            "Sale",
            "Limited",
        )
    }
    products = [
        Product(
            name="Linen shirt",
            price=Decimal("59.00"),
            tags=[tags["New"], tags["Organic"]],
        ),
        Product(
            name="Canvas tote",
            price=Decimal("24.00"),
            tags=[tags["Bestseller"], tags["Gift idea"]],
        ),
        Product(
            name="Wool scarf",
            price=Decimal("38.50"),
            tags=[tags["Handmade"], tags["Limited"], tags["Gift idea"]],
        ),
    ]

    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    statuses = list(OrderStatus)
    orders = []
    for index in range(24):
        items = [
            OrderItem(
                product=products[(index + line) % len(products)],
                quantity=1 + (index * 5 + line * 3) % 4,
                unit_price=products[(index + line) % len(products)].price,
            )
            for line in range(1 + (index * 7) % 3)
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
    return [*customers, *tags.values(), *products, *orders]
