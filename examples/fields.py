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
from typing import Any

from fastapi import FastAPI
from sqlalchemy import JSON, Column, ForeignKey, Integer, Numeric, String, Table, Text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import StaticPool

from adminsite import Admin, Computed, FieldOptions, Inline, ModelView
from adminsite.actions import Selection, action
from adminsite.auth import hash_password
from adminsite.backends.sqlalchemy import SessionAdapter
from adminsite.fields import (
    ChoiceField,
    EmailField,
    Field,
    FileField,
    ImageField,
    IntegerField,
    ListField,
    PasswordField,
    RelationField,
)
from adminsite.files import FileStorage, LocalStorage
from adminsite.views.writing import SaveContext

MEGABYTE = 1024 * 1024
EUROS = "€{:,.2f}"
GROUP = "Field gallery"


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


SIZES = (("S", "Small"), ("M", "Medium"), ("L", "Large"), ("XL", "Extra large"))
COLOURS = (
    ("black", "Black"),
    ("white", "White"),
    ("red", "Red"),
    ("green", "Green"),
    ("blue", "Blue"),
    ("yellow", "Yellow"),
)


def showcase_fields(
    storage: FileStorage, *, upload_limit: int = 5 * MEGABYTE
) -> tuple[Field | FieldOptions, ...]:
    """The showcase's fields, each saying which class it is.

    Its files are kept in `storage`, each no larger than `upload_limit`.
    """
    return (
        FieldOptions("name", help_text="StringField, required: one line of text."),
        FieldOptions("summary", help_text="TextField: a Text column gets a box."),
        EmailField("email", help_text="EmailField, chosen in the view."),
        PasswordField(
            "password",
            help_text="PasswordField: never shown. Leave it empty to keep the one set.",
        ),
        FieldOptions("quantity", help_text="IntegerField."),
        FieldOptions("weight", help_text="FloatField."),
        FieldOptions("price", format=EUROS, help_text="DecimalField, shown in euros."),
        FieldOptions("in_stock", help_text="BooleanField."),
        FieldOptions(
            "flagged",
            tones={True: "rose", False: None},
            help_text="BooleanField that may be left empty, rose when it is set.",
        ),
        FieldOptions("released_on", help_text="DateField."),
        FieldOptions("updated_at", help_text="DateTimeField."),
        FieldOptions("opens_at", help_text="TimeField."),
        FieldOptions("serial", help_text="UUIDField."),
        FieldOptions(
            "status",
            tones={
                Status.DRAFT: "grey",
                Status.ACTIVE: "green",
                Status.PAUSED: "amber",
                Status.RETIRED: "rose",
            },
            help_text="ChoiceField from an Enum column, a colour for each value.",
        ),
        ChoiceField(
            "size",
            choices=SIZES,
            help_text="ChoiceField with its own choices, on a String column.",
        ),
        ChoiceField(
            "colours",
            choices=COLOURS,
            multiple=True,
            help_text="ChoiceField with multiple=True, on a JSON column.",
        ),
        FieldOptions("specs", help_text="JSONField."),
        ListField(
            "scores",
            item=IntegerField("scores"),
            help_text="ListField of whole numbers, one on each line.",
        ),
        FileField(
            "manual",
            storage=storage,
            accept=".pdf,.txt",
            max_size=upload_limit,
            help_text="FileField: a PDF or a text file.",
        ),
        ImageField(
            "photo", storage=storage, max_size=upload_limit, help_text="ImageField."
        ),
        FieldOptions(
            "category", help_text="RelationField to one of five: a plain select."
        ),
        FieldOptions(
            "supplier", help_text="RelationField to one of 150: searched as you type."
        ),
        FieldOptions("labels", help_text="RelationField to many of twelve: chips."),
        FieldOptions(
            "stockists",
            help_text="RelationField to many of 150: chips, searched as you type.",
        ),
        FieldOptions(
            "servers",
            ordered=True,
            help_text="RelationField with ordered=True: tried in this order.",
        ),
        FieldOptions("created_at", help_text="DateTimeField, read only."),
        Computed(
            "stock_value",
            lambda showcase: showcase.price * showcase.quantity,
            label="Stock value",
            format=EUROS,
            help_text="Computed: the price times the quantity.",
        ),
    )


# Where uploads go, and where the sample photos are written for the records.
uploads = LocalStorage("fields_uploads")


class ShowcaseView(ModelView, model=Showcase):
    name = "fields"
    group = GROUP
    display_template = "{name}"
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
        "scores",
        "manual",
        "supplier",
        "stockists",
        "servers",
        "stock_value",
        "created_at",
    )
    search_fields = ("name", "email", "summary")
    list_filter = (
        "status",
        "in_stock",
        "flagged",
        "size",
        "category",
        "price",
        "released_on",
        "updated_at",
    )
    ordering = ("id",)
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

    async def before_save(self, context: SaveContext) -> None:
        # The password is form only: it reaches the record as a hash.
        password = context.values.get("password")
        if password:
            context.set("password_hash", hash_password(password))

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
    async def offer(self, selection: Selection, stockists: list[Supplier]) -> str:
        names = ", ".join(stockist.name for stockist in stockists) or "nobody"
        return f"{await selection.count()} offered to {names}."


class CategoryView(ModelView, model=Category):
    group = GROUP
    display_template = "{name}"
    search_fields = ("name",)


class SupplierView(ModelView, model=Supplier):
    group = GROUP
    display_template = "{name} ({country})"
    list_display = ("name", "country")
    search_fields = ("name", "country")
    list_filter = ("country",)


class LabelView(ModelView, model=Label):
    group = GROUP
    display_template = "{name}"
    search_fields = ("name",)


class ServerView(ModelView, model=Server):
    group = GROUP
    display_template = "{name}"
    search_fields = ("name",)


VIEWS: list[type[ModelView]] = [
    ShowcaseView,
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
    return [
        *categories,
        *suppliers,
        *labels,
        *servers,
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
