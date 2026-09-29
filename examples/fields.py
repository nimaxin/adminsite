"""Every kind of field adminsite has, on one model, each labelled by its class.

    uv run uvicorn examples.fields:app --reload

Then open http://127.0.0.1:8000/admin. There is nothing to sign in with, and
the records start over whenever the server does. The live demo shows the same
gallery beside its shop.
"""

import enum
import struct
import uuid
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired

import pydantic
from fastapi import FastAPI
from sqlalchemy import JSON, Column, ForeignKey, Integer, Numeric, String, Table, Text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import StaticPool
from typing_extensions import TypedDict

from adminsite import Admin, Inline, ModelView, SaveContext
from adminsite.actions import Selection, action
from adminsite.auth import hash_password
from adminsite.backends.sqlalchemy import SessionAdapter
from adminsite.fields import (
    BooleanField,
    ComputedField,
    EmailField,
    EnumField,
    Field,
    FileField,
    ImageField,
    IntegerField,
    JSONField,
    ListField,
    PasswordField,
    RelationField,
)
from adminsite.files import FileStorage, LocalStorage

MEGABYTE = 1024 * 1024
EUROS = "€{:,.2f}"
GROUP = "Field gallery"


def outline(paths: str) -> str:
    """A sidebar icon drawn the way the admin draws its own."""
    return (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )


class Base(DeclarativeBase):
    pass


class Status(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


def link_table(name: str, target: str) -> Table:
    """A link from a showcase to many records of one kind."""
    return Table(
        name,
        Base.metadata,
        # A serial id, so an ordered link reads back in the order it was saved.
        Column("id", Integer, primary_key=True),
        Column(
            "showcase_id",
            ForeignKey("showcases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column(
            f"{target}_id",
            ForeignKey(f"{target}s.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )


showcase_labels = link_table("showcase_labels", "label")
showcase_stockists = link_table("showcase_stockists", "supplier")
showcase_servers = link_table("showcase_servers", "server")


class Category(Base):
    """Five of them, so a link to one is a plain select."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))


class Supplier(Base):
    """150 of them, so a link to one or to many is searched on the server."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(40))


class Label(Base):
    """Twelve of them, so the picker holds the whole list in the page."""

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))


class Server(Base):
    """Eight of them, tried in turn, so the link keeps an order."""

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))


class Channel(TypedDict):
    """Where a shop posts its announcements."""

    id: int
    url: str
    title: NotRequired[str]


class PercentOff(pydantic.BaseModel):
    """A promotion that takes a share off."""

    kind: Literal["percent"] = "percent"
    percent: Annotated[int, pydantic.Field(gt=0, le=100)]


class AmountOff(pydantic.BaseModel):
    """A promotion that takes an amount off."""

    kind: Literal["amount"] = "amount"
    amount: Annotated[float, pydantic.Field(gt=0)]


class ShopSettings(pydantic.BaseModel):
    """What the settings column holds, and so what its form asks for."""

    open: bool = pydantic.Field(
        True, description="Customers can place orders while this is on."
    )
    free_shipping_over: Annotated[
        float, pydantic.Field(ge=0, description="Orders above this amount ship free.")
    ] = 50
    payment_methods: list[Literal["card", "cash", "transfer"]] = ["card"]
    exchange_rates: dict[
        Literal["USD", "EUR"], Annotated[float, pydantic.Field(gt=0)]
    ] = {}
    announcement_channels: list[Channel] = pydantic.Field(
        [], description="Channels that get each new announcement."
    )
    promotion: PercentOff | AmountOff | None = pydantic.Field(
        None, description="A share or an amount off, written as JSON."
    )


class Showcase(Base):
    """One column, or one link, for each kind of field."""

    __tablename__ = "showcases"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(default=0)
    weight: Mapped[float | None]
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    in_stock: Mapped[bool] = mapped_column(default=True)
    flagged: Mapped[bool | None]
    released_on: Mapped[date | None]
    updated_at: Mapped[datetime | None]
    opens_at: Mapped[time | None]
    serial: Mapped[uuid.UUID | None] = mapped_column(default=uuid.uuid4)
    status: Mapped[Status] = mapped_column(default=Status.DRAFT)
    size: Mapped[str | None] = mapped_column(String(2))
    colours: Mapped[list[str]] = mapped_column(JSON, default=list)
    specs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scores: Mapped[list[int]] = mapped_column(JSON, default=list)
    manual: Mapped[str | None] = mapped_column(String(255))
    photo: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    category: Mapped[Category | None] = relationship()
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    supplier: Mapped[Supplier | None] = relationship()
    labels: Mapped[list[Label]] = relationship(secondary=showcase_labels)
    stockists: Mapped[list[Supplier]] = relationship(secondary=showcase_stockists)
    servers: Mapped[list[Server]] = relationship(
        secondary=showcase_servers, order_by=showcase_servers.c.id
    )
    variants: Mapped[list["Variant"]] = relationship(
        back_populates="showcase", cascade="all, delete-orphan"
    )


class Variant(Base):
    """Rows edited inside a showcase's form."""

    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    showcase_id: Mapped[int] = mapped_column(ForeignKey("showcases.id"))
    showcase: Mapped[Showcase] = relationship(back_populates="variants")
    name: Mapped[str] = mapped_column(String(40))
    stock: Mapped[int] = mapped_column(default=0)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    supplier: Mapped[Supplier | None] = relationship()


class Setting(Base):
    """One of the shop's settings, whose value has the shape its key gives it."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class CustomerGroup(Base):
    """Customers who get some of the shop's settings changed for them."""

    __tablename__ = "customer_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    overrides: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Delivery(pydantic.BaseModel):
    """The value of the delivery setting."""

    carriers: list[Literal["dhl", "ups", "post"]] = ["post"]
    free_over: Annotated[float, pydantic.Field(ge=0)] = 50


class Maintenance(pydantic.BaseModel):
    """The value of the maintenance setting."""

    on: bool = pydantic.Field(
        False, description="Closes checkout and shows the message."
    )
    message: str = pydantic.Field("", json_schema_extra={"format": "textarea"})


class Returns(pydantic.BaseModel):
    """The value of the returns setting."""

    days: Annotated[int, pydantic.Field(ge=1, le=60)] = 30
    free: bool = True


SETTINGS: dict[str, type[pydantic.BaseModel]] = {
    "delivery": Delivery,
    "maintenance": Maintenance,
    "returns": Returns,
}


def setting_schema(setting: Setting) -> type[pydantic.BaseModel] | None:
    """The shape of a setting's value, by its key."""
    return SETTINGS.get(setting.key)


SIZES = (("S", "Small"), ("M", "Medium"), ("L", "Large"), ("XL", "Extra large"))
COLOURS = (
    ("black", "Black"),
    ("white", "White"),
    ("red", "Red"),
    ("green", "Green"),
    ("blue", "Blue"),
    ("yellow", "Yellow"),
)


def stock_value(showcase: Showcase) -> Decimal:
    return showcase.price * showcase.quantity


def showcase_fields(
    storage: FileStorage, *, upload_limit: int = 5 * MEGABYTE
) -> list[Field[Any] | ComputedField[Showcase, Any]]:
    """The showcase's fields, each saying which class it is.

    Its files are kept in `storage`, each no larger than `upload_limit`.
    """
    return [
        Field(Showcase.name, help_text="StringField, required: one line of text."),
        Field(Showcase.summary, help_text="TextAreaField: a Text column gets a box."),
        EmailField(Showcase.email, help_text="EmailField, chosen in the view."),
        PasswordField(
            "password",
            help_text="PasswordField: never shown. Leave it empty to keep the one set.",
        ),
        Field(Showcase.quantity, help_text="IntegerField."),
        Field(Showcase.weight, help_text="FloatField."),
        Field(Showcase.price, format=EUROS, help_text="DecimalField, shown in euros."),
        Field(Showcase.in_stock, help_text="BooleanField."),
        BooleanField(
            Showcase.flagged,
            tones={True: "rose", False: None},
            help_text="BooleanField that may be left empty, rose when it is set.",
        ),
        Field(Showcase.released_on, help_text="DateField."),
        Field(Showcase.updated_at, help_text="DateTimeField."),
        Field(Showcase.opens_at, help_text="TimeField."),
        Field(Showcase.serial, help_text="UUIDField."),
        EnumField(
            Showcase.status,
            tones={
                Status.DRAFT: "grey",
                Status.ACTIVE: "green",
                Status.PAUSED: "amber",
                Status.RETIRED: "rose",
            },
            help_text="EnumField from an Enum column, a colour for each value.",
        ),
        EnumField(
            Showcase.size,
            choices=SIZES,
            help_text="EnumField with its own choices, on a String column.",
        ),
        EnumField(
            Showcase.colours,
            choices=COLOURS,
            multiple=True,
            help_text="EnumField with multiple=True, on a JSON column.",
        ),
        Field(Showcase.specs, help_text="JSONField."),
        JSONField(
            Showcase.settings,
            schema=ShopSettings,
            help_text="JSONField with a schema: a form built from a Pydantic model.",
        ),
        ListField(
            Showcase.scores,
            item=IntegerField("scores"),
            help_text="ListField of whole numbers, one on each line.",
        ),
        FileField(
            Showcase.manual,
            storage=storage,
            accept=".pdf,.txt",
            max_size=upload_limit,
            help_text="FileField: a PDF or a text file.",
        ),
        ImageField(
            Showcase.photo,
            storage=storage,
            max_size=upload_limit,
            help_text="ImageField.",
        ),
        Field(
            Showcase.category, help_text="RelationField to one of five: a plain select."
        ),
        Field(
            Showcase.supplier,
            help_text="RelationField to one of 150: searched as you type.",
        ),
        Field(Showcase.labels, help_text="RelationField to many of twelve: chips."),
        Field(
            Showcase.stockists,
            help_text="RelationField to many of 150: chips, searched as you type.",
        ),
        RelationField(
            Showcase.servers,
            ordered=True,
            help_text="RelationField with ordered=True: tried in this order.",
        ),
        Field(Showcase.created_at, help_text="DateTimeField, read only."),
        ComputedField(
            "stock_value",
            stock_value,
            label="Stock value",
            format=EUROS,
            help_text="ComputedField: the price times the quantity.",
        ),
    ]


# Where uploads go, and where the sample photos are written for the records.
uploads = LocalStorage("fields_uploads")


class ShowcaseView(ModelView[Showcase]):
    name = "fields"
    group = GROUP
    # Three shapes, for every kind of field.
    icon = outline(
        '<path d="M12 3l4.5 7.5h-9z"/>'
        '<rect x="3.5" y="14" width="7" height="7" rx="1.2"/>'
        '<circle cx="17.5" cy="17.5" r="3.5"/>'
    )
    record_title = "{name}"
    list_display = (
        "name",
        "photo",
        "status",
        "price",
        "in_stock",
        "category",
        "labels",
        "released_on",
    )
    # Every other field, to add from the column picker.
    list_columns = (
        "summary",
        "email",
        "quantity",
        "weight",
        "flagged",
        "updated_at",
        "opens_at",
        "serial",
        "size",
        "colours",
        "specs",
        "settings",
        "scores",
        "manual",
        "supplier",
        "stockists",
        "servers",
        "stock_value",
        "created_at",
    )
    searchable_fields = ("name", "email", "summary")
    list_filters = (
        "status",
        "in_stock",
        "flagged",
        "size",
        "category",
        "price",
        "released_on",
        "updated_at",
    )
    fields_default_sort = ("id",)
    readonly_fields = ("created_at",)
    form_fields = (
        "name",
        "summary",
        "email",
        "password",
        "quantity",
        "weight",
        "price",
        "in_stock",
        "flagged",
        "released_on",
        "updated_at",
        "opens_at",
        "serial",
        "status",
        "size",
        "colours",
        "specs",
        "settings",
        "scores",
        "manual",
        "photo",
        "category",
        "supplier",
        "labels",
        "stockists",
        "servers",
        "created_at",
    )
    detail_fields = (
        *(name for name in form_fields if name != "password"),
        "stock_value",
    )
    inlines = (Inline("variants", fields=("name", "supplier", "stock")),)
    fields = showcase_fields(uploads)

    async def before_save(self, context: SaveContext[Showcase]) -> None:
        # The password is form only: it reaches the record as a hash.
        password = context.values["password"].get()
        if password:
            context.values[Showcase.password_hash].set(hash_password(password))

    @action(
        "Order from a supplier",
        on="record",
        inputs=[RelationField("supplier", target=Supplier, required=True)],
    )
    async def order_from(
        self, record: Showcase, session: SessionAdapter, supplier: Supplier
    ) -> str:
        return f"Ordered {record.name} from {supplier.name}."

    @action(
        "Offer to stockists",
        inputs=[RelationField("stockists", target=Supplier, collection=True)],
    )
    async def offer(
        self, selection: Selection[Showcase], stockists: list[Supplier]
    ) -> str:
        names = ", ".join(stockist.name for stockist in stockists) or "nobody"
        return f"{await selection.count()} offered to {names}."


class SettingView(ModelView[Setting]):
    group = GROUP
    # Sliders, for settings.
    icon = outline(
        '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
        '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/>'
        '<circle cx="17" cy="18" r="2"/>'
    )
    record_title = "{key}"
    list_display = ("key", "value")
    form_fields = ("key", "value")
    fields = (
        EnumField(
            Setting.key,
            choices=[(key, key.capitalize()) for key in SETTINGS],
            required=True,
            help_text="EnumField. The value's form follows the key chosen here.",
        ),
        JSONField(
            "value",
            schema=setting_schema,
            help_text="JSONField whose schema comes from the record: one for each key.",
        ),
    )


class CustomerGroupView(ModelView[CustomerGroup]):
    group = GROUP
    # Two people, for a group of customers.
    icon = outline(
        '<circle cx="9" cy="8" r="3.2"/>'
        '<path d="M3 20c.6-3.4 3-5.5 6-5.5s5.4 2.1 6 5.5"/>'
        '<path d="M16 5.2a3 3 0 0 1 0 5.6M18 14.8c1.6.7 2.7 2.4 3 5.2"/>'
    )
    record_title = "{name}"
    list_display = ("name", "overrides")
    form_fields = ("name", "overrides")
    fields = (
        JSONField(
            "overrides",
            schema=ShopSettings,
            partial=True,
            help_text="JSONField with partial=True: only what is set here is saved.",
        ),
    )


class CategoryView(ModelView[Category]):
    group = GROUP
    icon = outline(
        '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5'
        'a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>'
    )
    record_title = "{name}"
    searchable_fields = ("name",)


class SupplierView(ModelView[Supplier]):
    group = GROUP
    icon = outline(
        '<path d="M14 17V6H3v11h2"/><path d="M14 9h4l3 4v4h-2"/>'
        '<path d="M9 17h6"/><circle cx="7" cy="17.5" r="2"/>'
        '<circle cx="17" cy="17.5" r="2"/>'
    )
    record_title = "{name} ({country})"
    list_display = ("name", "country")
    searchable_fields = ("name", "country")
    list_filters = ("country",)


class LabelView(ModelView[Label]):
    group = GROUP
    icon = outline('<path d="M6 3h12v18l-6-4-6 4z"/>')
    record_title = "{name}"
    searchable_fields = ("name",)


class ServerView(ModelView[Server]):
    group = GROUP
    icon = outline(
        '<rect x="3" y="3" width="18" height="7" rx="1.5"/>'
        '<rect x="3" y="14" width="18" height="7" rx="1.5"/>'
        '<path d="M7 6.5h.01M7 17.5h.01"/>'
    )
    record_title = "{name}"
    searchable_fields = ("name",)


VIEWS: list[type[ModelView[Any]]] = [
    ShowcaseView,
    SettingView,
    CustomerGroupView,
    CategoryView,
    SupplierView,
    LabelView,
    ServerView,
]

CITIES = (
    "Berlin", "Paris", "Madrid", "Rome", "Vienna", "Oslo", "Warsaw", "Prague",
    "Lisbon", "Dublin", "Athens", "Sofia", "Tehran", "Istanbul", "Cairo",
)  # fmt: skip
LABELS = (
    "New", "Sale", "Gift", "Eco", "Handmade", "Limited", "Imported", "Local",
    "Bestseller", "Fragile", "Heavy", "Returned",
)  # fmt: skip
SERVERS = (
    "Frankfurt 1", "Frankfurt 2", "Amsterdam", "Helsinki", "Istanbul", "Dubai",
    "Tehran backup", "Local",
)  # fmt: skip
PHOTOS = {
    "teal": (20, 160, 150),
    "amber": (240, 170, 30),
    "rose": (220, 70, 110),
    "slate": (90, 100, 120),
    "violet": (130, 90, 220),
}


def square_png(colour: tuple[int, int, int], size: int = 96) -> bytes:
    """A picture of one colour, so the sample records have photos."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(colour) * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


def sample_file(uploads: Path, name: str, content: bytes) -> str:
    """Put a sample file where uploads go, and return its key as an upload would."""
    key = f"samples/{name}"
    path = uploads / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return key


def build_gallery(uploads: Path) -> list[Base]:
    """Records that use every field, with their photos written into `uploads`."""
    categories = [
        Category(name=name)
        for name in ("Phones", "Laptops", "Audio", "Cameras", "Accessories")
    ]
    suppliers = [
        Supplier(name=f"{city} Trading {number}", country=city)
        for city in CITIES
        for number in range(1, 11)
    ]
    labels = [Label(name=name) for name in LABELS]
    servers = [Server(name=name) for name in SERVERS]
    photos = {
        name: sample_file(uploads, f"{name}.png", square_png(colour))
        for name, colour in PHOTOS.items()
    }
    manual = sample_file(uploads, "manual.txt", b"How to use this item.\n")
    now = datetime.now().replace(second=0, microsecond=0)

    everything = Showcase(
        name="Everything filled in",
        summary=(
            "A longer piece of text, the kind a Text column holds.\n\n"
            "It keeps its paragraphs, and it runs on long enough to see how the "
            "list cuts it short and the record page shows it whole."
        ),
        email="orders@example.com",
        quantity=12,
        weight=1.25,
        price=Decimal("49.90"),
        in_stock=True,
        flagged=False,
        released_on=date(2026, 3, 14),
        updated_at=now - timedelta(hours=3),
        opens_at=time(9, 30),
        status=Status.ACTIVE,
        size="M",
        colours=["red", "black"],
        specs={"battery": "4000 mAh", "ports": ["USB-C", "HDMI"], "waterproof": True},
        settings={
            "open": True,
            "free_shipping_over": 75,
            "payment_methods": ["card", "cash"],
            "exchange_rates": {"USD": 1.08},
            "announcement_channels": [
                {"id": 1024, "url": "https://t.me/acme_news", "title": "News"},
                {"id": 2048, "url": "https://t.me/acme_deals"},
            ],
            "promotion": {"kind": "percent", "percent": 10},
        },
        scores=[7, 9, 10],
        manual=manual,
        photo=photos["teal"],
        category=categories[0],
        supplier=suppliers[3],
        labels=[labels[0], labels[4], labels[7]],
        stockists=[suppliers[10], suppliers[42], suppliers[77]],
        servers=[servers[2], servers[0], servers[5]],
        variants=[
            Variant(name="Small", stock=4, supplier=suppliers[20]),
            Variant(name="Large", stock=8),
        ],
    )
    required_only = Showcase(name="Only what is required", price=Decimal("1.00"))
    long_name = Showcase(
        name="A product with a name far too long to fit on one line of the list",
        price=Decimal("1299.00"),
        quantity=3,
        status=Status.RETIRED,
        flagged=True,
        in_stock=False,
        category=categories[1],
        photo=photos["rose"],
        labels=labels[:9],
    )

    statuses = list(Status)
    sizes = [code for code, _label in SIZES]
    colours = [code for code, _label in COLOURS]
    photo_keys = list(photos.values())
    more = [
        Showcase(
            name=f"Sample {index}",
            summary=f"Sample number {index}." if index % 2 else None,
            email=f"sample{index}@example.com" if index % 3 else None,
            quantity=index * 3,
            weight=round(0.4 * index, 2) if index % 4 else None,
            price=Decimal(9 * index) + Decimal("0.99"),
            in_stock=index % 3 != 0,
            flagged=(None, True, False)[index % 3],
            released_on=date(2026, 1, 1) + timedelta(days=17 * index),
            updated_at=now - timedelta(days=index, hours=index),
            opens_at=time(8 + index % 10, (index * 15) % 60),
            status=statuses[index % len(statuses)],
            size=sizes[index % len(sizes)],
            colours=colours[index % 6 : index % 6 + 1 + index % 3],
            specs={"generation": index} if index % 2 else None,
            scores=list(range(index % 5)),
            photo=photo_keys[index % len(photo_keys)] if index % 4 else None,
            category=categories[index % len(categories)],
            supplier=suppliers[(index * 13) % len(suppliers)],
            labels=[labels[index % 12], labels[(index + 5) % 12]],
            servers=[servers[index % 8], servers[(index + 3) % 8]],
        )
        for index in range(1, 12)
    ]
    settings = [
        Setting(key="delivery", value={"carriers": ["dhl", "ups"], "free_over": 75}),
        Setting(key="maintenance", value={"on": False, "message": "Back at 18:00."}),
    ]
    groups = [
        CustomerGroup(
            name="Wholesale buyers",
            overrides={"free_shipping_over": 0, "payment_methods": ["transfer"]},
        ),
    ]
    return [
        *categories,
        *suppliers,
        *labels,
        *servers,
        *settings,
        *groups,
        everything,
        required_only,
        long_name,
        *more,
    ]


# In memory, so the gallery starts over whenever the server does.
engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and fill them."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        session.add_all(build_gallery(uploads.directory))
        await session.commit()
    yield


app = FastAPI(title="Field gallery", lifespan=lifespan)

admin = Admin(
    engine,
    title="Field gallery",
    views=VIEWS,
    # Persian as well, to see the fields drawn right to left.
    languages=("fa",),
    secret_key="change-this-before-you-deploy-anything",
)
app.mount("/admin", admin)
