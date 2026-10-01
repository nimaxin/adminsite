from dataclasses import KW_ONLY, dataclass, field, fields, replace
from typing import Any, ClassVar, Generic, Self, TypeVar

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import QueryableAttribute

from adminsite.columns import Link, describe, written_path
from adminsite.exceptions import FieldValidationError
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema
from adminsite.text import as_text, humanize

__all__ = [
    "BaseField",
    "Field",
]

V = TypeVar("V")


# eq=False: a field is equal only to itself, and can be a key in a dict.
@dataclass(eq=False, repr=False)
class BaseField:
    """What every field has: a label, the pages it is left off, how it reads.

    `Field` and the kinds built on it show a column; `ComputedField` shows a
    value worked out from the record.
    """

    _: KW_ONLY
    # Left empty, the column's label, or the name written as words.
    label: str = ""
    help_text: str = ""
    # Left as None, the column decides: a value is required where the column
    # cannot be null and has no default.
    required: bool | None = None
    # Shown in the form and never changed there.
    read_only: bool = False
    # Left as None, the column's length, as String(80) gives it.
    max_length: int | None = None
    # What a new record's input starts with, and what an action's dialog
    # opens with. A stored value always wins over it. Any here, and the
    # column's type of value on Field and ComputedField.
    default: Any = None
    # How a value is written wherever it is shown, as `str.format` takes it:
    # "€{:,.2f}" for money. Inputs keep the plain value.
    format: str | None = None
    # Whether the audit log keeps "***" instead of the value, when the field
    # asks for one of an action's values. Left as None, a name such as
    # "password" or "api_key" decides.
    secret: bool | None = None
    # On the form under a name of its own, never read from the record or
    # written to it: its value reaches before_save and after_save, which
    # store it wherever it belongs.
    form_only: bool = False
    # The pages a view leaves the field off, as its exclude_fields_from_
    # lists do.
    exclude_from_list: bool = False
    exclude_from_detail: bool = False
    exclude_from_create: bool = False
    exclude_from_edit: bool = False
    exclude_from_export: bool = False
    # Offered in the list's Columns menu, and off the list until someone
    # turns it on.
    hidden_in_list: bool = False

    # The path the field shows, such as "total" or "customer.email".
    name: str = field(init=False)
    # Whether the label was given, rather than taken from the column, so a
    # column of a related model can name the relation as well.
    labelled: bool = field(init=False)
    _adapter: TypeAdapter[Any] = field(init=False)

    widget = "text"
    python_type: ClassVar[type[Any]] = str
    error_message = "Enter a valid value."
    # Whether the value is stored on the record. A computed field is not,
    # so it is never loaded, written, sorted or filtered.
    stored = True
    # Whether leaving the input empty on an existing record keeps what the
    # record has, instead of clearing it, as for a password.
    keeps_value_when_blank = False
    # The options above that this kind never reads. A view refuses one
    # written on the field, so a date field given max_length= stops the
    # admin starting instead of refusing every date typed in.
    unused_options: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        self.labelled = bool(self.label)
        if not self.labelled:
            self.label = humanize(self.name.rpartition(".")[2])
        if self.form_only:
            self.stored = False
        self._adapter = TypeAdapter(self.python_type)

    def display(self, value: Any) -> str:
        """Format the value for reading. An empty value shows as nothing."""
        if value is None:
            return ""
        return as_text(value)

    def text_for(self, record: Any, value: Any) -> str:
        """The text shown for this value, given the record it belongs to.

        Everything that shows a value goes through here: the list, the
        record page and the export. Override it where the text depends on
        another column, such as an amount that reads differently per
        currency; override `display` where the value alone is enough.
        """
        if self.format is not None and value is not None:
            return self.format.format(value)
        return self.display(value)

    def hint(self) -> str:
        """How to fill the input in, shown under it when there is no help text.

        A method, so it is worded in the language of each request.
        """
        return ""

    def serialize(self, value: Any) -> str:
        """Format the value for a form input."""
        if value is None:
            return ""
        return str(value)

    def parse(self, raw: str | None) -> Any:
        """Turn submitted text into a Python value, or raise."""
        text = raw.strip() if isinstance(raw, str) else raw
        if not text:
            if self.required:
                raise FieldValidationError(self.name, _("This field is required."))
            return None
        return self.to_python(text)

    def to_python(self, text: str) -> Any:
        """Convert non empty text, assuming it is present and stripped."""
        if self.max_length is not None and len(text) > self.max_length:
            raise FieldValidationError(
                self.name,
                _(
                    "Keep this to {count} characters or fewer.",
                    count=self.max_length,
                ),
            )
        try:
            return self._adapter.validate_python(text)
        except PydanticValidationError:
            raise FieldValidationError(self.name, _(self.error_message)) from None

    def _filled(self, options: dict[str, Any]) -> Self:
        """A copy holding options a column gave, for those left out.

        Always a copy, so the field written in the view stays as written. A
        label taken from the column is not one given, so a column of a
        related model still names the relation as well.
        """
        filled = replace(self, **options)
        filled.labelled = self.labelled
        return filled

    def check_options(self) -> None:
        """Refuse options that do not fit together, once the column filled its part.

        The view calls it for every field it is given, when it is built, so a
        mistake stops the admin starting rather than the page that shows it.
        """

    def __repr__(self) -> str:
        return f"{type(self).__name__}({describe(self.name)})"


@dataclass(eq=False, repr=False)
class Field(BaseField, Generic[V]):
    """A column's field: `Field(Order.created_at, label="Placed")`.

    On its own it is the field adminsite picks for the column, with the
    options given. The kinds, such as `DecimalField` or `TextAreaField`,
    are built on it and choose the field themselves. Either way, an option
    left out comes from the column: its label, its length, whether it may
    be empty.

    The column is named by its attribute, by a `Link` for a column of a
    related model, or by its name as a string.
    """

    column: QueryableAttribute[V] | Link[V] | str
    _: KW_ONLY
    default: V | None = None

    # The types of value this kind's column may hold, as V says them to a
    # type checker. A view checks a column named by a string against them
    # when it starts. Empty for a kind that takes any column.
    column_types: ClassVar[tuple[type[Any], ...]] = ()

    def __post_init__(self) -> None:
        self.name = written_path(self.column)
        super().__post_init__()

    def given_options(self) -> dict[str, Any]:
        """The options written on the field, to build another kind with."""
        written = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.init and item.name != "column"
        }
        if not self.labelled:
            written["label"] = ""
        return written

    def filled_from(self, schema: FieldSchema) -> Self:
        """A copy with what the column says wherever no option was given."""
        return self._filled(self.column_options(schema))

    def column_options(self, schema: FieldSchema) -> dict[str, Any]:
        """The options the column gives this field, for those left out."""
        options: dict[str, Any] = {}
        if not self.labelled:
            options["label"] = schema.label
        if self.required is None:
            options["required"] = schema.required
        if self.max_length is None:
            options["max_length"] = schema.max_length
        return options

    def __repr__(self) -> str:
        return f"{type(self).__name__}({describe(self.column)})"
