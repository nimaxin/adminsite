# Fields

A field shows a value, puts it into a form input, and reads it back when the form is saved.
adminsite picks one for every column from its type, so you rarely write one yourself.

The examples on this page use the [example models](models.md).

The Field gallery on [the live demo](https://adminsite.duckdns.org) shows every field on this
page, each labelled with its class.

| Column | Field | Shown as | Edited with |
|---|---|---|---|
| `str` with a length up to 255 | `StringField` | the text | a text input |
| `str` without a length, or longer | `TextAreaField` | the text | a text box |
| `int` | `IntegerField` | `42` | a number input |
| `float` | `FloatField` | `4.2` | a number input |
| `Decimal` | `DecimalField` | `1,234.50` | a number input |
| `bool` | `BooleanField` | `Yes` or `No` | a switch |
| `date` | `DateField` | `Sep 8, 2026` | a date picker |
| `datetime` | `DateTimeField` | `Sep 8, 2026 14:05` | a date and time picker |
| `time` | `TimeField` | `14:05` | a time picker |
| an enum | `EnumField` | `Shipped` | a select |
| a relationship | `RelationField` | the linked record's name | a picker |

Labels come from the column name, so `created_at` reads "Created at" and `customer_id` reads
"Customer". A value that does not fit, such as letters in a number, comes back on the form with a
message under the field. A `DateTimeField` shows times on the reader's clock, as
[Time zones](translations.md#time-zones) explains.

## Choose a field

`Field` is the field adminsite picks for the column, with your options:

```python
from adminsite import Field


class ProductView(ModelView[Product]):
    fields = [
        Field(Product.name, label="Product name"),
        Field(Product.description, help_text="Shown on the shop page."),
        Field(Product.price, format="€{:,.2f}", read_only=True),
    ]
```

A kind such as `EmailField` or `TextAreaField` picks the field itself:

```python
from adminsite.fields import EmailField, RelationField, TextAreaField


class CustomerView(ModelView[Customer]):
    fields = [
        Customer.name,
        EmailField(Customer.email),
        TextAreaField(Customer.notes, label="Internal notes"),
    ]


class OrderView(ModelView[Order]):
    fields = [
        Order.id,
        RelationField(Order.customer, record_title="{name} ({email})"),
    ]
```

Either way the field starts from its column: its label, the length of a `String(80)`, whether it
may be empty, an enum's choices and the model a relationship links to. An option you give wins. The
options are typed keywords, so a type checker refuses a misspelt one, or `TextAreaField(Order.total)`
on a `Decimal` column. A column of a type of your own, such as a `TypeDecorator`, gets the field of
the type it wraps.

Every field takes the options [`BaseField`][adminsite.BaseField] lists, such as `label`,
`help_text`, `read_only`, `default`, `format` and `secret`. With `format="€{:,.2f}"` the list, the
record page, the export and the dashboard read `€1,234.50`, while the form's input keeps the plain
number.

`EnumField` on a string column takes its options from `choices=[("S", "Small"), ("L", "Large")]`,
or from an Enum given as `enum=`. On an Enum column, `choices` relabel or narrow its members.

Without a label, a related model's column names the relation as well, so
`Link(Order.customer, Customer.name)` reads "Customer name".

## Badge colours

A choice and a yes or no are drawn as a coloured badge. Without `tones`, each choice takes a colour
by its place in the list. Give the field `tones` to say which colour means what:

```python
from adminsite.fields import BooleanField, EnumField


class OrderView(ModelView[Order]):
    fields = [
        EnumField(
            Order.status,
            tones={
                OrderStatus.PENDING: "amber",
                OrderStatus.SHIPPED: "green",
                OrderStatus.FAILED: "rose",
            },
        ),
        EnumField(Order.country, tones="grey"),
        BooleanField(Order.high_risk, tones={True: "rose", False: None}),
    ]
```

The tones are `amber`, `blue`, `green`, `grey`, `violet` and `rose`. A value left out is grey, and
`None` draws no badge, so a risk flag shows only when it is set. A single tone, such as `"grey"`,
colours every value alike. A value is named by its enum member or by the value the column stores.

## Say what an empty value means

An empty value often means something: a supplier with no closing date is still open, and a rule
with no regions picked applies in every region. Give the field `empty_text`, and the list and the
record page show it faint, like a placeholder, where they would show a hyphen, so it never reads
as a value someone set. The export writes it into the empty cell:

```python
from adminsite import Field


class SupplierView(ModelView[Supplier]):
    fields = [Supplier.name, Field(Supplier.closed_at, empty_text="Still open")]
```

A link to many records and a multi-select take it too, such as `empty_text="Every region"`. The
form, the filters and the search are unchanged, and an import reads the text back as empty.

## Relationships

A relationship shows the linked records' names in the list and a picker in the form. On a table of
more than 100 records the picker searches as you type, twenty records at a time, so give the other
model's view a `record_title` that tells its records apart.

The picker reads through the other model's view, so its `scope_query` and its permissions apply,
and it searches that view's `searchable_fields`. An `EnumField` with `multiple=True` gets the same
picker, over its options.

### Keep a link in order

Some links are kept in order, such as servers tried in turn. Mark the field `ordered`, and the
form shows its records as a numbered list to put in order:

```python
class ConfigView(ModelView[Config]):
    fields = [Config.name, RelationField(Config.proxies, ordered=True)]
```

The relationship needs an order of its own to keep. A link table with a serial id, read in the
order of that id, is the usual way:

```python
from sqlalchemy import Column, ForeignKey, Integer, Table
from sqlalchemy.orm import Mapped, relationship

config_proxies = Table(
    "config_proxies",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("config_id", ForeignKey("configs.id"), nullable=False),
    Column("proxy_id", ForeignKey("proxies.id"), nullable=False),
)


class Config(Base):
    ...
    proxies: Mapped[list[Proxy]] = relationship(
        secondary=config_proxies, order_by=config_proxies.c.id
    )
```

## JSON columns

A `JSON` or `JSONB` column gets a `JSONField`. The form edits the document in a code box that
checks it as you type, and the record page shows it laid out, with each part folding. A document
that does not parse comes back on the form, with its line and column named and the text kept as it
was written.

### A form from a schema

Settings kept as JSON are often described already, by a Pydantic model or a TypedDict. Give it to
the field as its `schema`, and the form asks for each part with the control its type calls for:

```python
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict  # typing's own on 3.12 and later

from adminsite.fields import JSONField


class Channel(TypedDict):
    id: int
    url: str
    title: NotRequired[str]


class ShopSettings(BaseModel):
    open: bool = True
    free_shipping_over: Annotated[
        float, Field(ge=0, description="Orders above this amount ship free.")
    ] = 50
    payment_methods: list[Literal["card", "cash", "transfer"]] = ["card"]
    exchange_rates: dict[Literal["USD", "EUR"], Annotated[float, Field(gt=0)]] = {}
    announcement_channels: list[Channel] = []


class ShopView(ModelView[Shop]):
    fields = [Shop.name, JSONField(Shop.settings, schema=ShopSettings)]
```

| In the schema | In the form |
| --- | --- |
| `bool` | A switch. |
| `int`, `float`, `Decimal` | A number input, with limits such as `ge=0` checked. |
| `str`, `EmailStr`, `HttpUrl`, `date`, `datetime`, `time` | An input of that kind, with lengths and `pattern` checked. |
| `Literal[...]` or an `Enum` | A select, or the picker for a list of them. |
| A list of plain values | A box with one value per line. |
| A model or a TypedDict inside | Its properties under its title. |
| A list of models or TypedDicts | A table whose rows can be added and removed. |
| A `dict` | A table of keys and values. |
| Anything else, such as a choice between two models | A code box for that part. |

Labels come from the property names, so `free_shipping_over` reads "Free shipping over", and a
property's `description` is the note under it. When the form is saved, Pydantic checks the document,
so your model's validators run, and each problem shows beside its input. What is saved is the
document as Pydantic writes it, with its defaults filled in.

A few things to know:

- A JSON Schema written as a dict works too. Each part's own field checks it then.
- Keys a stored document holds that the schema does not name are kept, unless the schema refuses
  them.
- A stored value the schema cannot hold, such as a list where it asks for an object, opens in the
  code box, so none of it is lost.
- The [JSON API](api.md) checks a document against the same schema.
- Titles and descriptions come from your schema, so adminsite does not translate them.

### A schema for each record

When the rows of one table hold documents of different shapes, such as a settings table with a row
per setting, give a function that returns each record's schema, or `None` for the code box:

```python
SCHEMAS: dict[str, type[BaseModel]] = {"delivery": Delivery, "maintenance": Maintenance}


def schema_for(setting: Setting) -> type[BaseModel] | None:
    return SCHEMAS.get(setting.key)


class SettingView(ModelView[Setting]):
    fields = [Setting.key, JSONField(Setting.value, schema=schema_for)]
```

On the form for a new record, the function gets an unsaved record holding what the form holds so
far, and the document's part of the form is drawn again when the key changes.

### Partial documents

An override stores only what it changes, such as a customer group that gets free shipping whatever
the shop's own settings say. With `partial=True` each property may be left unset:

```python
class CustomerGroupView(ModelView[CustomerGroup]):
    fields = [
        CustomerGroup.name,
        JSONField(CustomerGroup.overrides, schema=ShopSettings, partial=True),
    ]
```

The form shows the properties the document sets, and **Add a field** sets another. A property left
unset is left out of the document.

## Lists of values

A Postgres `ARRAY` column gets a `ListField`. The form takes one value per line, each read by the
field its type calls for, and the other pages show the values separated by commas. A JSON column
holding a list works the same way, on any database:

```python
from adminsite.fields import IntegerField, ListField


class ProductView(ModelView[Product]):
    fields = [
        Product.name,
        ListField(Product.keywords),
        ListField(Product.sizes, item=IntegerField("sizes")),
    ]
```

## Computed values

`ComputedField` shows a value the view works out from the record, in the list, on the record page
and in the export:

```python
from adminsite.fields import ComputedField


def capacity(product: Product) -> str:
    return f"{len(product.slots)}/{product.limit}"


class ProductView(ModelView[Product]):
    fields = [
        Product.name,
        Product.price,
        ComputedField("capacity", capacity, needs=[Product.slots]),
    ]
```

`needs` names what the function reads, so it is loaded with the page instead of once per row. A
computed value is shown, and never written, sorted or filtered.

### A value from a query

A count or a sum over related rows needs a query. Give `load` instead of a function: an async
function that gets the session and every record on the page, and answers with each record's value
by its primary key. It runs once for the whole page:

```python
from collections.abc import Sequence

from sqlalchemy import func, select

from adminsite.database import SessionAdapter


async def member_counts(
    session: SessionAdapter, groups: Sequence[Group]
) -> dict[int, int]:
    rows = await session.execute(
        select(Member.group_id, func.count())
        .where(Member.group_id.in_([group.id for group in groups]))
        .group_by(Member.group_id)
    )
    return {group_id: count for group_id, count in rows.all()}


class GroupView(ModelView[Group]):
    fields = [Group.name, ComputedField("members", load=member_counts, default=0)]
```

A record the answer leaves out gets `default`, here 0 for a group with no members.

## Form-only inputs

A form can hold an input that is not a column, such as a password to hash or settings kept in
another table. Give its field `form_only=True`. Its value reaches `before_save` and `after_save` in
`context.values`, and your [hooks](hooks.md) store it where it belongs. No other page shows it.

### A password

`PasswordField` is a form-only input that is never filled in. On a record that exists, leaving it
empty keeps the password, and `password` is then not in `context.values`:

```python
from adminsite import RefusedError, SaveContext
from adminsite.fields import PasswordField


class UserView(ModelView[User]):
    fields = [User.email, PasswordField("password", required=True)]

    async def before_save(self, context: SaveContext[User]) -> None:
        password = context.values["password"].get()
        if password is None:
            return
        if len(password) < 12:
            raise RefusedError("Use 12 characters or more.", field="password")
        context.values[User.password_hash].set(hash_password(password))
```

`required` holds for a new record. The audit log keeps `***` for a password, and the
[JSON API](api.md) takes one and never sends it back.

### A value kept in another table

A form-only field starts empty. `form_only_values` fills it in from somewhere else. It gets the
session and the record, or `None` on the form for a new one:

```python
from typing import Any

from sqlalchemy import delete, select
from starlette.requests import Request

from adminsite.database import SessionAdapter
from adminsite.fields import JSONField


class GroupView(ModelView[Group]):
    fields = [Group.name, JSONField("settings", form_only=True)]

    async def form_only_values(
        self, session: SessionAdapter, record: Group | None, *, request: Request
    ) -> dict[str, Any]:
        if record is None:
            return {}
        rows = await session.scalars(
            select(GroupSetting).where(GroupSetting.group_id == record.id)
        )
        return {"settings": {row.name: row.value for row in rows}}

    async def after_save(self, context: SaveContext[Group]) -> None:
        settings = context.values["settings"].get()
        if settings is None:
            return
        group = context.record
        await context.session.execute(
            delete(GroupSetting).where(GroupSetting.group_id == group.id)
        )
        for name, value in settings.items():
            await context.session.add(
                GroupSetting(group_id=group.id, name=name, value=value)
            )
```

`after_save` runs in the save's transaction, once a new group has its `id`, so a refusal undoes the
rows and the record together.

## A value that depends on the record

A field's `display(value)` sees only the value, but an amount reads differently per currency.
Override `text_for`, which gets the record too:

```python
from decimal import Decimal

from adminsite.fields import DecimalField


class Money(DecimalField):
    def text_for(self, record: Order, value: Decimal | None) -> str:
        if value is None:
            return ""
        return f"{value:,.2f} {record.currency}"
```

The list, the record page and the export all show a value through `text_for`, which falls back to
`display`.

## HTML in a cell

A cell is escaped text. Return `Html` where it is meant to be markup, such as a link to another
system:

```python
from adminsite import Html, Link
from adminsite.fields import ComputedField


def tracking(order: Order) -> Html:
    return Html('<a class="link" href="{}">Track</a>').format(order.tracking_url)


class OrderView(ModelView[Order]):
    fields = [
        Order.id,
        Link(Order.customer, Customer.name),
        ComputedField("tracking", tracking, needs=[Order.tracking_url]),
    ]
```

`Html` keeps its own text as markup and escapes everything put in with `format` or `%`, so a value
from the database cannot carry markup into the page. Write the markup yourself and pass the values
through `format`. The CSV export and the JSON API get the same cell without its tags.

## Files and pictures

A file field keeps the upload in a storage and its key in a string column:

```python
from adminsite.fields import FileField, ImageField
from adminsite.files import LocalStorage

uploads = LocalStorage("uploads")


class ProductView(ModelView[Product]):
    fields = [
        Product.name,
        ImageField(Product.photo, storage=uploads),
        FileField(
            Product.datasheet,
            storage=uploads,
            accept=".pdf",
            max_size=20 * 1024 * 1024,
        ),
    ]
```

The form shows the current file and a box to remove it, and the list shows a thumbnail or a link.
`storage` says where the files go, `accept` which types are taken and `max_size` the largest file,
as [`FileField`][adminsite.fields.FileField] says. `ImageField` takes PNG, JPEG, GIF and WebP, and
checks each file's first bytes. It refuses SVG, which can carry script.

A file is stored under a key such as `2026/09/k3j9x2-datasheet.pdf`, so make the column
`String(255)`. A new upload replaces the old file once the save has committed, and deleting a
record keeps its files.

Files are served through the admin, behind the sign in. If your app already serves the folder, say
where, and links point there:

```python
uploads = LocalStorage("uploads", url_prefix="https://cdn.example.com/uploads")
```

To keep files somewhere else, such as S3, subclass `FileStorage`: `save` stores an upload and
returns its key, `delete` removes one, and `url` or `response` say how a file is fetched.

File fields do not work inside [inlines](views.md#edit-related-records-in-the-same-form) yet.

## Your own field type

Subclass `Field` with the type its column holds, and say how to show and read the value:

```python
from adminsite.exceptions import FieldValidationError
from adminsite.fields import Field


class PercentField(Field[float | None]):
    widget = "number"
    python_type = float
    error_message = "Enter a percentage, for example 12.5."

    def display(self, value: float | None) -> str:
        return "" if value is None else f"{value:.1f}%"

    def to_python(self, text: str) -> float:
        number: float = super().to_python(text.rstrip("%"))
        if not 0 <= number <= 100:
            raise FieldValidationError(self.name, "Enter a value between 0 and 100.")
        return number
```

Fields are dataclasses, so options of your own go after `_: KW_ONLY`, under
`@dataclass(eq=False, repr=False)`, as the built-in kinds do. To use the field for every column of
a type, register it:

```python
from adminsite.fields import default_registry

default_registry.register(Percentage, PercentField)
```

The input is drawn by its `widget`. [Customizing the look](customizing.md#draw-a-widget-your-way) shows how to
draw your own.
