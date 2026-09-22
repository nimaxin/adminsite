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

Every field takes `label`, `required`, `readonly`, `help_text` and `max_length`.

## Links to many records

A relationship that holds many records, such as a customer's orders, shows the linked records'
names in the list and a multiple select in the form. On a table with more than 100 records the
picker becomes a search box, served by a lookup that searches the other model's text columns.

## JSON columns

A `JSON` or `JSONB` column gets a `JSONField` by itself. The list shows the document on one line,
cut short where it is long, and the form edits it in a box, laid out over several lines. A document
that does not parse comes back as an error on that field, with the text exactly as it was written,
so nothing is lost and nothing malformed is stored.

The [JSON API](api.md) reads and writes these columns as JSON, so `{"options": {"free_over": 10}}`
is stored as an object, not as a string.

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

A computed value is never written, sorted or filtered: its column has no sort link, the form
leaves it out, and so do imports and the API's writes. The API still reads it.

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
