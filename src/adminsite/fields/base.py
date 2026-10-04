from dataclasses import KW_ONLY, dataclass, field, fields, replace
from typing import Any, ClassVar, Generic, Self, TypeVar

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import QueryableAttribute

from adminsite._text import humanize
from adminsite.columns import Link, describe, written_path
from adminsite.exceptions import FieldValidationError
from adminsite.i18n import gettext as _
from adminsite.markup import as_text
from adminsite.schema import FieldSchema

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
    value worked out from the record. Every kind takes the options below,
    and refuses one it never reads when the admin starts, such as
    `max_length` on a date. A field of your own subclasses `Field` and says
    how to show and read its value with the methods below.

    Args:
        label: The field's label. Left empty, the column's label, or the
            name written as words: `created_at` reads "Created at".
        help_text: A line under the input, saying how to fill it in.
        required: Whether the form refuses the field left empty. Left as
            None, the column decides: required where it cannot be null and
            has no default.
        read_only: Shown on the form and never read back from it, even if
            someone adds the input back by hand.
        max_length: The most characters the input takes. Left as None, the
            column's length, as `String(80)` gives it.
        default: What a new record's input starts with, and what an
            action's dialog opens with. A stored value always wins over it.
        format: How the value is written wherever it is shown, as
            `str.format` takes it: `"€{:,.2f}"` for money. The input keeps
            the plain value.
        secret: Whether the audit log keeps `***` instead of the value, in
            what a save changed and what an action was run with. Left as
            None, a name such as `password` or `api_key` decides.
        form_only: Whether the field is an input that is no column. It is
            on the form under a name of its own, never read from the record
            or written to it, and its value reaches `before_save` and
            `after_save`, which store it where it belongs.
        exclude_from_list: Leaves the field off the list.
        exclude_from_detail: Leaves the field off the record page.
        exclude_from_create: Leaves the field off the form for a new record.
        exclude_from_edit: Leaves the field off the edit form.
        exclude_from_export: Leaves the field out of the CSV export.
        hidden_in_list: Offers the field in the list's Columns menu, and
            keeps it off the list until someone turns it on.
    """

    _: KW_ONLY
    label: str = ""
    help_text: str = ""
    required: bool | None = None
    read_only: bool = False
    max_length: int | None = None
    # Any here, and the column's type of value on Field and ComputedField.
    default: Any = None
    format: str | None = None
    secret: bool | None = None
    form_only: bool = False
    exclude_from_list: bool = False
    exclude_from_detail: bool = False
    exclude_from_create: bool = False
    exclude_from_edit: bool = False
    exclude_from_export: bool = False
    hidden_in_list: bool = False

    name: str = field(init=False)
    """The path the field shows, such as "total" or "customer.email"."""
    labelled: bool = field(init=False)
    """Whether the label was given, rather than taken from the column.

    A column of a related model names the relation as well where it was not.
    """
    _adapter: TypeAdapter[Any] = field(init=False)

    widget = "text"
    """The name of the input the form draws, such as "text" or "number".

    It picks the template `adminsite/widgets/<widget>.html`, or a plain
    input where there is none. A project can override one or add its own.
    """
    python_type: ClassVar[type[Any]] = str
    """The type `to_python` checks submitted text against, with Pydantic."""
    error_message = "Enter a valid value."
    """What the form says when the text is not a value of `python_type`."""
    stored = True
    """Whether the value is stored on the record.

    A computed field is not, so it is never loaded, written, sorted or
    filtered.
    """
    keeps_value_when_blank = False
    """Whether an input left empty on a record that exists keeps its value.

    True for a password, so a form saved without one keeps the old.
    """
    unused_options: ClassVar[frozenset[str]] = frozenset()
    """The options this kind never reads, refused when the admin starts.

    So a date field given `max_length` stops the admin starting instead of
    refusing every date typed in.
    """

    def __post_init__(self) -> None:
        self.labelled = bool(self.label)
        if not self.labelled:
            self.label = humanize(self.name.rpartition(".")[2])
        if self.form_only:
            self.stored = False
        self._adapter = TypeAdapter(self.python_type)

    def display(self, value: Any) -> str:
        """Format the value for reading. An empty value shows as nothing.

        Args:
            value: The value, as the record holds it.

        Returns:
            The text the list, the record page and the export show.
        """
        if value is None:
            return ""
        return as_text(value)

    def text_for(self, record: Any, value: Any) -> str:
        """The text shown for this value, given the record it belongs to.

        Everything that shows a value goes through here: the list, the
        record page and the export. Override it where the text depends on
        another column, such as an amount that reads differently per
        currency; override `display` where the value alone is enough.

        Args:
            record: The record the value belongs to.
            value: The value, as the record holds it.

        Returns:
            The text shown, in the field's `format` where it has one.
        """
        if self.format is not None and value is not None:
            return self.format.format(value)
        return self.display(value)

    def hint(self) -> str:
        """How to fill the input in, shown under it when there is no help text.

        A method, so it is worded in the language of each request.

        Returns:
            The hint, or nothing.
        """
        return ""

    def serialize(self, value: Any) -> str:
        """Format the value for a form input.

        Args:
            value: The value, as the record holds it.

        Returns:
            The text the input starts with, which `parse` reads back.
        """
        if value is None:
            return ""
        return str(value)

    def parse(self, raw: str | None) -> Any:
        """Turn submitted text into a Python value, or raise.

        Empty text is None, or refused where the field is required. Anything
        else goes to `to_python`, stripped.

        Args:
            raw: The text submitted for the field, or None when it is
                missing.

        Returns:
            The value to store.

        Raises:
            FieldValidationError: When the text is not a value the field
                takes, with a message for the form.
        """
        text = raw.strip() if isinstance(raw, str) else raw
        if not text:
            if self.required:
                raise FieldValidationError(self.name, _("This field is required."))
            return None
        return self.to_python(text)

    def to_python(self, text: str) -> Any:
        """Convert non empty text, assuming it is present and stripped.

        Override it to read a value of your own, and call it through
        `super()` to keep the checks on length and type.

        Args:
            text: The submitted text, stripped and not empty.

        Returns:
            The value to store.

        Raises:
            FieldValidationError: When the text is too long or not of
                `python_type`, with a message for the form.
        """
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

        Raises:
            AdminSiteError: When two options contradict each other, such as
                a tone given for a value the field does not have.
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

    Every option of `BaseField` applies as well.

    Args:
        column: The column, by its attribute, by a `Link` for a column of a
            related model, or by its name as a string.
        default: What a new record's input starts with, of the column's type
            of value.
    """

    column: QueryableAttribute[V] | Link[V] | str
    _: KW_ONLY
    default: V | None = None

    column_types: ClassVar[tuple[type[Any], ...]] = ()
    """The types of value this kind's column may hold, as `V` says them.

    A view checks a column named by a string against them when it starts.
    Empty for a kind that takes any column.
    """

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
