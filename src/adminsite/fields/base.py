from typing import Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from adminsite.exceptions import FieldValidationError
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema
from adminsite.text import as_text, humanize


class Field:
    """One value on a record, shown in a list and edited in a form."""

    widget = "text"
    python_type: type[Any] = str
    error_message = "Enter a valid value."
    # Whether the value is stored on the record. A computed field is not,
    # so it is never loaded, written, sorted or filtered.
    stored = True
    # Whether leaving the input empty on an existing record keeps what the
    # record has, instead of clearing it, as for a password.
    blank_keeps = False

    def __init__(
        self,
        name: str,
        *,
        label: str | None = None,
        required: bool = False,
        readonly: bool = False,
        help_text: str = "",
        max_length: int | None = None,
        default: Any = None,
        format: str | None = None,
        secret: bool | None = None,
        form_only: bool = False,
    ) -> None:
        self.name = name
        self.label = label if label is not None else humanize(name)
        self.required = required
        self.readonly = readonly
        self.help_text = help_text
        self.max_length = max_length
        # What a new record's input starts with, and what an action's
        # dialog opens with. A stored value always wins over it.
        self.default = default
        # How a value is written wherever it is shown, as `str.format` takes
        # it: "€{:,.2f}" for money. Inputs keep the plain value.
        self.format = format
        # Whether the audit log keeps "***" instead of the value, when the
        # field asks for one of an action's values. Left as None, a name
        # such as "password" or "api_key" decides.
        self.secret = secret
        # On the form under a name of its own, never read from the record or
        # written to it: its value reaches before_save and after_save, which
        # store it wherever it belongs.
        self.form_only = form_only
        if form_only:
            self.stored = False
        self._adapter: TypeAdapter[Any] = TypeAdapter(self.python_type)

    @classmethod
    def from_schema(cls, schema: FieldSchema, **overrides: Any) -> "Field":
        """Build a field from an inspected column."""
        options: dict[str, Any] = {
            "label": schema.label,
            "required": schema.required,
            "readonly": schema.primary_key,
            "max_length": schema.max_length,
        }
        options.update(overrides)
        return cls(schema.name, **options)

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

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"
