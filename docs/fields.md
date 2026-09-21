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
