# Fields

A field turns a value into text for the list, puts it into a form input, and reads it back when
the form is submitted. adminsite picks one for every column from its type, so you rarely write one
yourself.

To see them all, open the Field gallery on [the live demo](https://adminsite.duckdns.org): one
model with every field on this page, each saying which class it is. The same gallery runs on
your machine with `uv run uvicorn examples.fields:app --reload`.

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
| a relationship | `RelationField` | the linked record's name | a picker, or a search box on big tables |

Labels come from the column name: `created_at` reads "Created at", and `customer_id` reads
"Customer".

## When a value does not fit

A value that cannot be converted comes back on the form, with a message under the field and
everything else still filled in:

| Field | Message |
|---|---|
| a required field left empty | This field is required. |
| `IntegerField` | Enter a whole number. |
| `DecimalField` | Enter an amount, for example 12.50. |
| a string longer than its column | Keep this to 120 characters or fewer. |
| `EnumField` | Choose one of the listed options. |

## Choosing a field

A view's `fields` holds its columns, and a field wherever a column needs options. `Field` on its
own is the field adminsite picks for the column, with your options:

```python
from adminsite import Field


class ProductView(ModelView[Product]):
    fields = [
        Field(Product.name, label="Product name"),
        Field(Product.description, help_text="Shown on the shop page."),
        Field(Product.price, format="€{:,.2f}", read_only=True),
    ]
```

A kind such as `EmailField` or `TextAreaField` chooses the field itself:

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

Either way the field starts from its column. What the options leave out comes from there: the
label, the length of a `String(80)`, whether the value may be empty, an enum's choices, and the
model a relationship links to. So choosing a kind never means saying those again, and an option you
give wins over the column.

`EnumField` on a string column takes its options from `choices=[("S", "Small"), ("L", "Large")]`,
or from an Enum given as `enum=`. A string column stores a member's value, so the options an enum
gives it are keyed by the value: `EnumField(Shirt.size, enum=Size)` saves `"l"` for `Size.LARGE`.
A choice field with none of them, on a column that is not an Enum, has nothing to offer, and stops
the admin when it starts.

On an Enum column, `choices` relabel or narrow the column's members, and the field still hands
your hooks the member. A choice names a member by its name or by its value, in any case, so
`("PAID", "Paid up")` and `("paid", "Paid up")` both stand for `OrderStatus.PAID`. A choice that
names no member stops the admin when it starts.

The column is named by its attribute, by a [`Link`](views.md#naming-columns) for a column of a
related model, or by its name as a string. Options are keywords, each with its type, so a type
checker refuses `Field(Order.note, lable="Note")`, `Field(Order.note, "Note")` and
`TextAreaField(Order.total)` on a `Decimal` column, and Python refuses a misspelt option when the
module is imported. A column named by a string gets past the type checker, so the admin checks the
kind against the column when it starts: `TextAreaField("total")` stops it, and so does
`RelationField(Order.customer_id)`, which names the key instead of the relationship,
`Order.customer`. A column of a type of your own, such as a `TypeDecorator`, names no Python type to
check against, so any kind may show it: `DateTimeField("starts_at")` on a `TZDateTime` column gives
it a date and time picker.

Every field takes the options [`BaseField`][adminsite.BaseField] lists, such as `label`,
`help_text`, `read_only`, `default`, `format`, `secret` and the flags that leave it off a page. A
kind refuses one it never reads when the admin starts: `max_length` means nothing to a number, a
date or a time, a yes or no, a UUID, a choice, a relationship, a JSON document, a list or a file,
and a `ComputedField` is never in a form, so it takes nothing about one. `form_only` is for
[an input that is not a column](#inputs-that-are-not-columns).

With `format="€{:,.2f}"` the list, the record page, the export and the overview's cards read
`€1,234.50`, as a dashboard's `Stat` and `Chart` do with theirs. The form's input keeps the plain
number, since that is what it reads back.

A label you give is used as it stands. Without one, a column of a related model names the relation
as well, so `Link(Order.customer, Customer.name)` reads Customer name.

## Badge colours

A status, any other choice, and a yes or no are drawn as a coloured badge. By default a choice
takes the colour of its place in the list: the first is amber, then blue, green, grey, violet and
rose. A yes is green and a no grey. Give the field `tones` to say which colour means what:

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

The six tones are `amber`, `blue`, `green`, `grey`, `violet` and `rose`. A value left out is
grey, and `None` draws no badge at all, so a risk flag shows only when it is set. A single tone,
such as `"grey"`, colours every value alike, which suits choices that are categories rather than
states.

A value is named by its enum member, or by the value the column stores, in any case. A tone or a
value the field does not know is an error when the view is created, listing what it does know. The
list, a phone's card, the record page and the badge beside a record's title all draw a value the
same way.

## Links to many records

A relationship that holds many records, such as an article's tags, shows the linked records'
names in the list and a picker in the form. The picker is one box: the records it holds sit in it
as chips, in the order they were picked, each with a button to take it off, and a list opens under
it as soon as it is clicked or typed in. Picking a record adds it at the end and leaves the list
open for the next, a held one carries a check mark, and picking it again lets it go. The arrow
keys move through the list, Enter picks, Escape closes it, and Backspace in an empty box takes off
the last chip.

For a table of up to 100 records the whole list is in the page and narrows as you type. Above that
the list is searched on the server, twenty records at a time, and says so when more match. A link
that holds a single record works the same way above 100 records: the box shows the record's name,
typing searches for another, and picking replaces it. Below 100 it is a plain select. An
`EnumField` with `multiple=True` gets the same picker, over its options.

The search box is the only way the picker can work on a large table, so the records it offers are
whatever the lookup finds, twenty at a time. Give the other model's view a `record_title` so those
twenty read as something more telling than `Order #12`.

A picker reads through the other model's own view, so its `scope_query` and its permissions apply
here as on any other page. A user who may see only their own region's customers sees only those in
the picker, and a view nobody may open offers nothing at all. Where the other model has no view of
its own, there is nothing to ask and its records are read directly.

The search looks in the target view's `searchable_fields`, less any its `can_access_field` keeps
from the user on the list. Where that leaves none, it looks in the text columns the records are
named by, which the picker is already showing. So a column a view keeps off its pages cannot be
read a letter at a time through a picker, and a target with neither `searchable_fields` nor a
`record_title` cannot be narrowed at all.

### When the order means something

Some links are kept in order: servers tried in turn, a first choice and its fallbacks. Mark such a
link `ordered`, and the form shows its records as a numbered list that can be put in order, with
buttons to move one up or down or by dragging it. The box under the list adds one at the end, and
offers only the records the list does not hold yet:

```python
class ConfigView(ModelView[Config]):
    fields = [Config.name, RelationField(Config.proxies, ordered=True)]
```

The order has to live somewhere, so the relationship needs one of its own. A link table with a
serial id, read in the order of that id, is the usual way:

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

Saving a link only adds and removes what changed, so the rows kept stay where they were and new
ones go last. When that would lose the order given, as after moving one up, adminsite deletes the
link's rows and writes them again in the order shown, so their ids follow it. The record page, the
form and the API all read the link in the relationship's order. A link that is not marked
`ordered` keeps the order it has.

## JSON columns

A `JSON` or `JSONB` column gets a `JSONField` by itself. The list shows the document on one line,
cut short where it is long.

The form edits it in a code box, laid out over several lines, with line numbers and each part in
its colour: names, text, numbers, and `true`, `false` and `null`. Tab indents, Enter keeps the
indent, and a bracket or a quote closes itself. **Format** lays out a document that was pasted in
on one line. As you type, the box checks the document and says what is wrong and where, such as
"Line 4, column 20: a comma or } is missing."; clicking that puts the cursor on the mistake. Tab
stays in the box, so press Esc and then Tab to move on from the keyboard.

A document that does not parse when the form is saved comes back as an error on that field, naming
its line and column, with the text exactly as it was written, so nothing is lost and nothing
malformed is stored.

The record page shows the whole document, laid out and coloured the same way. Each object and list
folds on its first line and, folded, says how much it holds, such as `{ … } 3 keys`. A long
document opens with everything below its first level folded. **Fold all** and **Copy** sit above
it.

The [JSON API](api.md) reads and writes these columns as JSON, so `{"options": {"free_over": 10}}`
is stored as an object, not as a string.

### A form built from a schema

Settings kept as JSON are usually described already, by a Pydantic model or a TypedDict. Give that
to the field as its `schema`, and the form asks for each part with the control its type calls for,
instead of a code box:

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

Each part is drawn by one of the ordinary fields, so it looks and reads like the rest of the form:

| In the schema | In the form |
| --- | --- |
| `bool` | A switch. |
| `int`, `float`, `Decimal` | A number input; its limits, such as `ge=0`, are checked and named under it. |
| `str`, and `EmailStr`, `HttpUrl`, `date`, `datetime`, `time` | An input of that kind; `max_length`, `min_length` and `pattern` are checked. |
| `Literal[...]` or an `Enum` | A select. |
| A list of those | The picker, holding several. |
| A list of plain values | A box with one value per line. |
| A model or a TypedDict inside | Its properties under its title. |
| A list of models or TypedDicts | A table whose rows can be added and removed. |
| A `dict` | A table of keys and values, the keys chosen from a fixed set where they are a `Literal`. |
| A `Literal` with one value | Nothing to ask: the value is written as it is. |
| Anything else, such as a choice between two models | A code box for that part. |

Labels come from the property names, written as sentences, "Free shipping over", unless a property
has a title of its own. A property's `description` is the note under it.

When the form is saved the document is checked by Pydantic, so your model's own validators run.
Each problem appears beside the input it concerns, and in the list at the top of the form, such as
"Settings, Announcement channels, row 2, Id: This field is required."; nothing is saved, and every
input keeps what was typed. What is saved is the document as Pydantic writes it, with its defaults
filled in. A new record opens with the schema's defaults.

**JSON**, beside the field's label, shows the document the form stands for, exactly as Save would
write it, or what needs another look first.

The record page names each value by its title, lists and maps as small tables, and **JSON** there
shows the document itself, laid out and folding as above.

A few things to know:

- A JSON Schema written as a dict works as well as a Pydantic type. There is no Pydantic to run
  then, so the checks are those of each part's own field: types, limits, lengths and choices.
- Keys a stored document holds that the schema does not name are kept, unless the schema refuses
  them. A Pydantic model drops them unless its `model_config` allows extra keys.
- A stored value the schema cannot hold, such as a list where it asks for an object, is edited in
  the code box instead, so none of it is lost.
- The [JSON API](api.md) checks a document against the same schema, and names each problem by where
  it is: `{"settings.free_shipping_over": "Enter 0 or more."}`.
- Titles and descriptions come from your schema, so adminsite does not translate them.

### A schema for each record

Where the rows of one table hold documents of different shapes, such as a settings table with a
row per setting, give a function instead. It is given the record and returns its schema, or None
for a record that has none, whose value is then edited in the code box:

```python
SCHEMAS: dict[str, type[BaseModel]] = {"delivery": Delivery, "maintenance": Maintenance}


def schema_for(setting: Setting) -> type[BaseModel] | None:
    return SCHEMAS.get(setting.key)


class SettingView(ModelView[Setting]):
    fields = [Setting.key, JSONField(Setting.value, schema=schema_for)]
```

Two settings then open as two different forms. A new record has no row yet, so the function is
given an unsaved one that holds what the rest of the form holds so far: its own columns, read as
they are typed, without its links. When something outside the document changes, such as the key
chosen, the document's part of the form is drawn again for the new schema. The record is never
saved from there, and the function should only read it.

### Overrides: properties left unset

An override stores only what it changes and inherits the rest, as a customer group that pays by
transfer and gets free shipping, whatever the shop's own settings say. With `partial=True` each
property of the document may be set or left unset:

```python
class CustomerGroupView(ModelView[CustomerGroup]):
    fields = [
        CustomerGroup.name,
        JSONField(CustomerGroup.overrides, schema=ShopSettings, partial=True),
    ]
```

The form draws only the properties the document sets, each with **Remove** beside it. Under them,
**Add a field** opens a list of the others in the schema's order, each with its description, and
typing narrows it by either. One added appears in its own place and takes the focus. A property
left unset sends nothing and is left out of the document, so a group that sets two of fifty
settings saves those two keys and shows those two rows. A property that is set starts at its
default and is checked as usual; with a Pydantic model its validators run for each property that
is set. Other Pydantic types, and JSON Schema dicts, are checked by each part's own field.

The record page lists what is set, then a line such as **Not set (48)**, which opens to name the
rest.

## Lists

A Postgres `ARRAY` column, such as tags or country codes, gets a `ListField` by itself. The form
takes one value per line, and each value is read by the field its type calls for: `ARRAY(Integer)`
takes whole numbers, `ARRAY(String(2))` two letters at most, and a mistake is named by its line.
The list, the record page and the export show the values on one line, separated by commas. An
empty box stores an empty list, so a column that cannot be null still takes it.

The [JSON API](api.md) reads and writes a list as a JSON list, and an [import](import.md) takes the
values separated by commas, as the export writes them, or one per line. An array of arrays stays a
JSON document.

A JSON column holding a list can be edited the same way, on any database:

```python
from adminsite.fields import IntegerField, ListField


class ProductView(ModelView[Product]):
    fields = [
        Product.name,
        ListField(Product.keywords),
        ListField(Product.sizes, item=IntegerField("sizes")),
    ]
```

## A value the view works out

Not every column on a page is a column. `ComputedField` shows something the view works out from
the record, in the list, on the record page and in the export:

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

`needs` names what the function reads, so it is loaded with the page. Without it a list of 25
records would ask the database 25 times. The function takes the view's model, so a type checker
refuses one written for another model.

### A value that takes a query

A count, a sum or a breakdown of related rows is not something to load row by row and count in
Python. Give `load` instead of a function: an async function handed the session and every record on
the page, which answers with each record's value by its primary key. It runs once for the page,
however many rows the page holds:

```python
from collections.abc import Sequence

from sqlalchemy import func, select

from adminsite.backends.sqlalchemy import SessionAdapter


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

A record the answer leaves out gets `default`, here 0 for a group with no members. The value shows
in the list, on the record page, in the export and in the API, each loading it the same way. A
composite key is looked up as a tuple of its values.

A computed value is never written, sorted or filtered: its column has no sort link, the form
leaves it out, and so do imports and the API's writes. The API still reads it. A `ComputedField`
cannot be `form_only`: an input that is not a column is a field of its own, as the next section
shows.

## Inputs that are not columns

A form can hold an input that is not a column: a password to hash, a value that belongs in another
table, settings saved as rows. Give its field `form_only=True`. Its value is never read from the
record or written onto it. It reaches `before_save` and `after_save` in `context.values`, and the
[hooks](hooks.md) store it where it belongs. The record page, the list, the export and the API's
answers leave it out, and imports do not offer it.

### A password

`PasswordField` is a form-only input for a password. It is never filled in, not even after a
failed save, and on a record that exists, leaving it empty keeps the password there:

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

`required` holds for a new record. On one that exists the input says "Leave it empty to keep the
current one.", and left empty, `password` is not in `context.values` at all, so `get()` reads
None. A password is kept as
typed, spaces included, but spaces alone count as empty. A refusal naming the field shows beside
it.

The hash never shows. `password_hash` is not on the form, and the audit log keeps `***` for it, so
a change reads `Password hash: *** → ***`. The [JSON API](api.md) takes `password` in a `POST` or
a `PATCH` the same way, and never sends it back. As one of an
[action's inputs](actions.md), a `PasswordField` is kept out of the log as well.

### A value kept somewhere else

A form-only field starts empty. To start it from somewhere else, answer `form_only_values`,
which is given the session and the record, or None on the form for a new one:

```python
from typing import Any

from sqlalchemy import delete, select
from starlette.requests import Request

from adminsite.backends.sqlalchemy import SessionAdapter
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

`after_save` runs once the record is flushed, so a new group already has its `id`. It runs inside
the same transaction as the save, so a refusal from either hook undoes the rows and the record
together.

## When a field needs the whole record

`display(value)` sees only the value, which is not always enough: an amount reads differently per
currency, and a status reads differently when a second column says the check was switched off.
Override `text_for` instead, which gets the record:

```python
from decimal import Decimal

from adminsite.fields import DecimalField


class Money(DecimalField):
    def text_for(self, record: Order, value: Decimal | None) -> str:
        if value is None:
            return ""
        return f"{value:,.2f} {record.currency}"
```

Everything that shows a value goes through `text_for`: the list, the record page and the export.
It falls back to `display`, so fields that do not need the record carry on as they are.

## A link or a badge in a cell

Cells are escaped text, so a name holding `<script>` shows as it was written and nothing else.
Return `Html` where the cell is meant to be markup, such as a link to a file or to another system:

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

`Html` writes its own text into the page as markup. Everything put in with `format` or `%` is
escaped first, so a value out of the database cannot carry markup of its own into the page. Write
the markup yourself and the values through `format`, never the other way round.

The CSV export and the JSON API send the same cell without its tags, since markup belongs on a page
and not in a spreadsheet. The first column is already a link to the record, so put markup in
another one.

## Files and pictures

A file field keeps the upload in a storage and its key in a string column. Declare it among the
view's `fields`:

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

The form gets a file input with the current file, a thumbnail for pictures and a box to remove
it. The list shows a thumbnail or a link, and the detail page a larger picture.

`storage` says where the files go, `accept` which types are taken, checked on the server too, and
`max_size` the largest file: 10 MB for files and 5 MB for pictures unless you say. See
[`FileField`][adminsite.fields.FileField].

`ImageField` takes PNG, JPEG, GIF and WebP, and checks the file's first bytes, so a script renamed
to `.png` is refused. SVG is left out because it can carry script.

**Keys.** A file is stored under a key such as `2026/09/k3j9x2-datasheet.pdf`: the month, a random
part so names never clash, and the original name, cleaned of anything that could climb out of the
folder. Make the column long enough for it, `String(255)` is plenty.

**Serving.** By default files are served through the admin at `/admin/-/files/...`, behind the sign
in, to whoever may open the view. Pictures and PDFs open in the browser; anything else is sent as a
download, so an uploaded HTML file can never run in the admin. If your application already serves
the folder, say where, and links point there instead:

```python
uploads = LocalStorage("uploads", url_prefix="https://cdn.example.com/uploads")
```

**Replacing and removing.** A new upload replaces the old file, which is deleted once the save has
committed; a save that fails leaves the old file and throws the new one away. Deleting a record
keeps its files, so nothing is lost by accident.

**Other storages.** Subclass `FileStorage` to keep files elsewhere, such as S3. `save` stores an
upload and returns its key, `delete` removes one, and `url` or `response` say how it is fetched,
for example with a redirect to a signed URL.

File fields are not available inside [inlines](views.md#related-records-in-the-same-form) yet.

## Your own field type

Subclass `Field`, with the type of value its column holds, and say how to show and read the value:

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

Fields are dataclasses. A field with options of its own declares them as the built-in kinds do,
keyword-only, after `_: KW_ONLY`, under `@dataclass(eq=False, repr=False)`. To use it for every
column of a type, register it:

```python
from adminsite.fields import default_registry

default_registry.register(Percentage, PercentField)
```

How the input looks is decided by its `widget` name; see
[Customizing the look](customizing.md#one-widget) to draw your own.
