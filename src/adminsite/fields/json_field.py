import json
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.i18n import gettext as _

# Longer than this a document is cut short in a list cell.
CELL_LENGTH = 120


class JSONField(Field):
    """A JSON column: an object, a list, or any other JSON document.

    Chosen for JSON columns by itself. The form edits the document in a
    box, pretty printed, and a malformed one comes back as an error on the
    field with the text still there.
    """

    widget = "json"
    python_type = dict
    error_message = 'Write valid JSON, such as {"key": "value"}.'

    def display(self, value: Any) -> str:
        """The document on one line, cut short where a cell needs it."""
        if value is None:
            return ""
        text = json.dumps(value, ensure_ascii=False, sort_keys=False)
        return text if len(text) <= CELL_LENGTH else f"{text[: CELL_LENGTH - 1]}…"

    def serialize(self, value: Any) -> str:
        """The document laid out over several lines, for the box.

        Text goes back as it was typed, so a document that failed to parse
        comes back to its writer exactly as they left it.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)

    def to_python(self, text: str) -> Any:
        """Read the text as a JSON document."""
        try:
            return json.loads(text)
        except ValueError:
            raise FieldValidationError(self.name, _(self.error_message)) from None
