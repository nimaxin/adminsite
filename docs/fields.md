# Fields

A field turns a value into text for the list, puts it into a form input, and reads it back when
the form is submitted. adminsite picks one for every column from its type, so you rarely write one
yourself.

| Column | Field | Shown as | Edited with |
|---|---|---|---|
| `str` with a length up to 255 | `StringField` | the text | a text input |
| `str` without a length, or longer | `TextField` | the text | a text box |
| `int` | `IntegerField` | `42` | a number input |
| `float` | `FloatField` | `4.2` | a number input |
| `Decimal` | `DecimalField` | `1,234.50` | a number input |
| `bool` | `BooleanField` | `Yes` or `No` | a switch |
| `date` | `DateField` | `Sep 8, 2026` | a date picker |
| `datetime` | `DateTimeField` | `Sep 8, 2026 14:05` | a date and time picker |
| `time` | `TimeField` | `14:05` | a time picker |
| an enum | `ChoiceField` | `Shipped` | a select |
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
| `ChoiceField` | Choose one of the listed options. |

## Replacing a field

Give the view a field object with the same name as the path, and it replaces the one adminsite
would have built:

```python
from adminsite.fields import EmailField, RelationField, TextField


class CustomerView(ModelView, model=Customer):
    fields = (
        EmailField("email", required=True),
        TextField("notes", label="Internal notes", help_text="Only staff see this."),
    )


class OrderView(ModelView, model=Order):
    fields = (
        RelationField(
            "customer",
            target=Customer,
            required=True,
            display_template="{name} ({email})",
        ),
    )
```

Every field takes `label`, `required`, `readonly`, `help_text`, `max_length`, `default`, `format`,
`secret` and `form_only`. `default` is what a new record's form starts with, and what an action's
dialog opens with. `secret` says whether the [audit log](audit.md#secrets) keeps `***` instead of
the value, in what a save changed and in what an action was run with; left out, a name such as
`password_hash` or `api_key` decides. `form_only` is for
[an input that is not a column](#inputs-that-are-not-columns). `format` is how a value is written
wherever it is shown, as `str.format` takes it, the same way a dashboard's `Stat` and `Chart` take
it:

```python
from adminsite import FieldOptions


class OrderView(ModelView, model=Order):
    fields = (FieldOptions("total", format="€{:,.2f}"),)
```

The list, the record page, the export and the overview's cards then read `€1,234.50`. The form's
input keeps the plain number, since that is what it reads back.

## Changing one thing about a field

Most of the time the field adminsite worked out is the right one and only its label or a line of
help is wrong. `FieldOptions` changes those without naming the type, the target or anything else
again:

```python
from adminsite import FieldOptions


class ProductView(ModelView, model=Product):
    fields = (
        FieldOptions("name", label="Product name"),
        FieldOptions("description", help_text="Shown on the shop page."),
    )


class OrderView(ModelView, model=Order):
    fields = (FieldOptions("customer", display_template="{name} ({email})"),)
```

It takes whatever the field takes, so `label`, `required`, `readonly`, `help_text` and
`max_length` work on any field, and `display_template` works on a link. An option the field does
not take is an error when the view first builds it, naming the path, rather than a setting that
quietly does nothing.

`FieldOptions` sits in the same `fields` tuple as whole fields. Where both name the same path the
whole field wins, since it already says everything.

A label given this way is used as it stands. Without one, a path through a link names the link as
well, so `customer.name` reads Customer name.

## Links to many records

A relationship that holds many records, such as a customer's orders, shows the linked records'
names in the list and a multiple select in the form.

Above 100 records the select is no good, so the picker becomes a search box. It searches the other
model's text columns through a lookup, and the records already linked sit above it as chips, each
with a button to take it off. Picking a record adds one more, picking the same one twice changes
nothing, and the box says how many are held. A link that holds a single record works the same way,
except that picking replaces what is there.

The search box is the only way the picker can work on a large table, so the records it offers are
whatever the lookup finds, twenty at a time. Give the other model's view a `display_template` so those
twenty read as something more telling than `Order #12`.

A picker reads through the other model's own view, so its `scope_query` and its permissions apply
here as on any other page. A user who may see only their own region's customers sees only those in
the picker, and a view nobody may open offers nothing at all. Where the other model has no view of
its own, there is nothing to ask and its records are read directly.

The search looks in the target view's `search_fields`. Where it names none, it looks in the text
columns the records are named by, which the picker is already showing. So a column a view keeps off
its pages cannot be read a letter at a time through a picker, and a target with neither
`search_fields` nor a `display_template` cannot be narrowed at all.

## JSON columns

A `JSON` or `JSONB` column gets a `JSONField` by itself. The list shows the document on one line,
cut short where it is long, and the form edits it in a box, laid out over several lines. A document
that does not parse comes back as an error on that field, with the text exactly as it was written,
so nothing is lost and nothing malformed is stored.

The [JSON API](api.md) reads and writes these columns as JSON, so `{"options": {"free_over": 10}}`
is stored as an object, not as a string.

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


class ProductView(ModelView, model=Product):
    fields = (
        ListField("tags"),
        ListField("sizes", item=IntegerField("sizes")),
    )
```

## A value the view works out

Not every column on a page is a column. `Computed` shows something the view works out from the
record, in the list, on the record page and in the export:

```python
from adminsite import Computed


class ProductView(ModelView, model=Product):
    list_display = ("name", "price", "capacity")
    fields = (
        Computed(
            "capacity",
            lambda product: f"{len(product.slots)}/{product.limit}",
            label="Capacity",
            needs=("slots",),
        ),
    )
```

`needs` names the paths the function reads, so they are loaded with the page. Without it a list of
25 records would ask the database 25 times.

### A value that takes a query

A count, a sum or a breakdown of related rows is not something to load row by row and count in
Python. Give `load` instead of a function: an async function handed the session and every record on
the page, which answers with each record's value by its primary key. It runs once for the page,
however many rows the page holds:

```python
from sqlalchemy import func, select


async def member_counts(session, groups):
    rows = await session.execute(
        select(Member.group_id, func.count())
        .where(Member.group_id.in_([group.id for group in groups]))
        .group_by(Member.group_id)
    )
    return dict(rows.tuples().all())


class GroupView(ModelView, model=Group):
    list_display = ("name", "members")
    fields = (Computed("members", load=member_counts, default=0),)
```

A record the answer leaves out gets `default`, here 0 for a group with no members. The value shows
in the list, on the record page, on the form, in the export and in the API, each loading it the
same way. A composite key is looked up as a tuple of its values.

A computed value is never written, sorted or filtered: its column has no sort link, the form
leaves it out, and so do imports and the API's writes. The API still reads it.

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
from adminsite import RefusedError
from adminsite.fields import PasswordField
from adminsite.views.writing import SaveContext


class AccountView(ModelView, model=Account):
    form_fields = ("email", "password")
    fields = (PasswordField("password", required=True),)

    async def before_save(self, context: SaveContext) -> None:
        password = context.values.get("password")
        if password is None:
            return
        if len(password) < 12:
            raise RefusedError("Use 12 characters or more.", field="password")
        context.set("password_hash", hash_password(password))
```

`required` holds for a new record. On one that exists the input says "Leave it empty to keep the
current one.", and left empty, `password` is not in `context.values` at all. A password is kept as
typed, spaces included, but spaces alone count as empty. A refusal naming the field shows beside
it.

The hash never shows. `password_hash` is not on the form, and the audit log keeps `***` for it, so
a change reads `Password hash: *** → ***`. The [JSON API](api.md) takes `password` in a `POST` or
a `PATCH` the same way, and never sends it back. As one of an
[action's inputs](actions.md), a `PasswordField` is kept out of the log as well.

### A value kept somewhere else

A form-only field starts empty. To start it from somewhere else, answer `form_values`, which is
given the session and the record, or None on the form for a new one:

```python
from sqlalchemy import delete, select

from adminsite.fields import JSONField


class GroupView(ModelView, model=Group):
    form_fields = ("name", "settings")
    fields = (JSONField("settings", form_only=True),)

    async def form_values(self, session, record, *, request=None):
        if record is None:
            return {}
        rows = await session.scalars(
            select(GroupSetting).where(GroupSetting.group_id == record.id)
        )
        return {"settings": {row.name: row.value for row in rows}}

    async def after_save(self, context: SaveContext) -> None:
        settings = context.values.get("settings")
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
class Money(Field):
    widget = "number"

    def text_for(self, record, value):
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
from adminsite import Computed, Html


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "tracking")
    fields = (
        Computed(
            "tracking",
            lambda order: Html('<a class="link" href="{}">Track</a>').format(
                order.tracking_url
            ),
            needs=("tracking_url",),
        ),
    )
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


class ProductView(ModelView, model=Product):
    fields = (
        ImageField("photo", storage=uploads),
        FileField(
            "datasheet", storage=uploads, accept=".pdf", max_size=20 * 1024 * 1024
        ),
    )
```

The form gets a file input with the current file, a thumbnail for pictures and a box to remove
it. The list shows a thumbnail or a link, and the detail page a larger picture.

| Option | What it does |
|---|---|
| `storage` | Where the files go. |
| `accept` | The types taken, as in a browser's `accept`: extensions such as `.pdf`, types such as `application/pdf`, or `image/*`. Checked on the server too. |
| `max_size` | The largest file in bytes. 10 MB for files and 5 MB for pictures unless you say. |

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

Subclass `Field` and say how to show and read the value:

```python
from adminsite.exceptions import FieldValidationError
from adminsite.fields import Field


class PercentField(Field):
    widget = "number"
    python_type = float
    error_message = "Enter a percentage, for example 12.5."

    def display(self, value):
        return "" if value is None else f"{value:.1f}%"

    def to_python(self, text):
        number = super().to_python(text.rstrip("%"))
        if not 0 <= number <= 100:
            raise FieldValidationError(self.name, "Enter a value between 0 and 100.")
        return number
```

To use it for every column of a type, register it:

```python
from adminsite.fields import default_registry

default_registry.register(Percentage, PercentField)
```

How the input looks is decided by its `widget` name; see
[Customizing the look](customizing.md#one-widget) to draw your own.
