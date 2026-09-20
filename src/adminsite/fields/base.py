from typing import Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from adminsite.exceptions import FieldValidationError
from adminsite.schema import FieldSchema


class Field:
    """One value on a record, shown in a list and edited in a form."""

    widget = "text"
    python_type: type[Any] = str
    error_message = "Enter a valid value."

    def __init__(
        self,
        name: str,
        *,
        label: str | None = None,
        required: bool = False,
        readonly: bool = False,
        help_text: str = "",
        max_length: int | None = None,
    ) -> None:
        self.name = name
        self.label = label if label is not None else name
        self.required = required
        self.readonly = readonly
        self.help_text = help_text
        self.max_length = max_length
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
        return str(value)

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
                raise FieldValidationError(self.name, "This field is required.")
            return None
        return self.to_python(text)

    def to_python(self, text: str) -> Any:
        """Convert non empty text, assuming it is present and stripped."""
        if self.max_length is not None and len(text) > self.max_length:
            raise FieldValidationError(
                self.name,
                f"Keep this to {self.max_length} characters or fewer.",
            )
        try:
            return self._adapter.validate_python(text)
        except PydanticValidationError:
            raise FieldValidationError(self.name, self.error_message) from None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"
