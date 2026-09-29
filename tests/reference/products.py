"""A large view: many fields, a custom filter, and actions that ask for values."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from starlette.datastructures import UploadFile
from starlette.requests import Request

from adminsite import (
    Field,
    Link,
    Message,
    ModelView,
    Permission,
    RefusedError,
    SaveContext,
)
from adminsite.actions import Input, Selection, action
from adminsite.backends.sqlalchemy import (
    SessionAdapter,
    SQLAlchemyRepository,
    SQLFilter,
)
from adminsite.fields import (
    ComputedField,
    DecimalField,
    EnumField,
    ImageField,
    JSONField,
    TextAreaField,
)
from adminsite.files import LocalStorage
from adminsite.filters import FilterContext, FilterOption, FilterValue
from tests.reference.models import Product, ProductStatus, Supplier

EUROS = "€{:,.2f}"
MEGABYTE = 1024 * 1024
PHOTOS = LocalStorage("uploads/products")
COUNTRIES = [("DE", "Germany"), ("NL", "Netherlands"), ("FR", "France")]


class Dimensions(BaseModel):
    """What the dimensions column holds, drawn as a form of its own."""

    width_cm: float
    height_cm: float
    weight_kg: float | None = None


def margin_of(product: Product) -> str:
    if not product.price:
        return ""
    return f"{(product.price - product.cost) / product.price:.0%}"


def stock_level(product: Product) -> str:
    if product.stock == 0:
        return "Sold out"
    return "Running low" if product.stock <= product.reorder_level else "In stock"


def is_manager(request: Request) -> bool:
    return bool(request.session.get("manager"))


@dataclass(frozen=True)
class PriceChange:
    """How to change the chosen products' prices."""

    percent: Annotated[
        Decimal, Input(label="Change by (%)", help_text="Negative to lower prices.")
    ]
    round_to: Annotated[Decimal, Input(label="Round to")] = Decimal("0.05")
    never_below_cost: bool = True


@dataclass(frozen=True)
class PriceListFormat:
    """How to read an uploaded price list."""

    has_header: bool = True
    delimiter: Literal[",", ";"] = ","


class RestockFilter(SQLFilter[Product]):
    """Products that need ordering, or are gone already."""

    async def options(self, context: FilterContext) -> list[FilterOption]:
        return [FilterOption("low", "Running low"), FilterOption("out", "Sold out")]

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Product]
    ) -> ColumnElement[bool]:
        if value.first == "out":
            return Product.stock == 0
        return Product.stock <= Product.reorder_level


class SupplierView(ModelView[Supplier]):
    fields = [
        Supplier.name,
        Supplier.email,
        EnumField(Supplier.country, choices=COUNTRIES, tones="grey"),
        Supplier.products,
    ]
    searchable_fields = [Supplier.name, Supplier.email]

    def get_record_title(self, supplier: Supplier, /) -> str:
        return f"{supplier.name} ({supplier.country})"


class ProductView(ModelView[Product]):
    group = "Catalogue"
    fields = [
        Product.sku,
        Product.name,
        Product.supplier,
        Link(Product.supplier, Supplier.country),
        EnumField(
            Product.status,
            tones={
                ProductStatus.DRAFT: "grey",
                ProductStatus.LIVE: "green",
                ProductStatus.RETIRED: None,
            },
        ),
        DecimalField(Product.price, format=EUROS),
        DecimalField(Product.cost, format=EUROS, exclude_from_list=True),
        ComputedField("margin", margin_of, label="Margin"),
        Product.stock,
        ComputedField("stock_level", stock_level, label="Stock"),
        Field(
            Product.reorder_level,
            help_text="At or below this, the product shows as running low.",
            hidden_in_list=True,
        ),
        TextAreaField(Product.description),
        ImageField(
            Product.photo,
            storage=PHOTOS,
            max_size=2 * MEGABYTE,
            exclude_from_export=True,
        ),
        Product.tags,
        JSONField(Product.dimensions, schema=Dimensions, exclude_from_list=True),
        Field(
            Product.created_at,
            hidden_in_list=True,
            exclude_from_create=True,
            exclude_from_edit=True,
        ),
        Field(Product.updated_at, exclude_from_create=True, exclude_from_edit=True),
    ]
    exclude_fields_from_list = [Product.description, Product.tags]
    exclude_fields_from_export = [Product.cost]
    searchable_fields = [
        Product.sku,
        Product.name,
        Link(Product.supplier, Supplier.name),
    ]
    # Strings name columns too, and are checked when the admin starts;
    # "-updated_at" sorts newest first, as Descending(Product.updated_at) would.
    sortable_fields = [Product.name, Product.price, Product.stock, "updated_at"]
    fields_default_sort = [Product.name, "-updated_at"]
    list_filters = [
        Product.status,
        Link(Product.supplier, Supplier.country),
        RestockFilter("restock", label="Restock"),
    ]
    record_title = "{sku} {name}"
    can_delete = False
    can_import = True

    async def allows(
        self,
        action: Permission | str,
        *,
        request: Request,
        record: Product | None,
    ) -> bool:
        if action == "change_prices":
            return is_manager(request)
        retired = record is not None and record.status is ProductStatus.RETIRED
        return not (action == Permission.EDIT and retired)

    async def before_save(self, context: SaveContext[Product]) -> None:
        sku = context.values[Product.sku]
        sku.set(sku.get().strip().upper())
        if context.values[Product.price].get() < context.values[Product.cost].get():
            raise RefusedError(
                "Keep the price at or above the cost.", field=Product.price
            )

    @action("Change prices", permission="change_prices")
    async def change_prices(
        self, selection: Selection[Product], *, change: PriceChange
    ) -> str:
        products = await selection.records()
        factor = 1 + change.percent / 100
        for product in products:
            price = (product.price * factor / change.round_to).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            ) * change.round_to
            if change.never_below_cost:
                price = max(price, product.cost)
            product.price = price
        return f"{len(products)} prices changed."

    @action("Retire", dangerous=True, confirm="Retire the chosen products?")
    async def retire(self, selection: Selection[Product]) -> str:
        products = await selection.records()
        for product in products:
            product.status = ProductStatus.RETIRED
        return f"{len(products)} products retired."

    @action("Order more", on="record")
    async def order_more(
        self,
        product: Product,
        *,
        supplier: Annotated[Supplier, Input(label="From")],
        quantity: int = 10,
    ) -> str:
        product.stock += quantity
        return f"Ordered {quantity} of {product.name} from {supplier.name}."

    @action("Count what runs low", on="view", permission=Permission.VIEW)
    async def count_running_low(self, session: SessionAdapter) -> str:
        running_low = await session.scalar(
            select(func.count())
            .select_from(Product)
            .where(Product.stock <= Product.reorder_level)
        )
        return f"{running_low} products are running low."

    @action("Import a price list", on="view", permission="change_prices")
    async def import_prices(
        self,
        *,
        prices: Annotated[UploadFile, Input(label="Price list", accept=".csv")],
        read_as: PriceListFormat,
    ) -> Message:
        text = (await prices.read()).decode()
        rows = text.splitlines()[1:] if read_as.has_header else text.splitlines()
        return Message(
            f"{len(rows)} prices read from {prices.filename}.",
            link="/admin/products",
            link_text="See the products",
        )
