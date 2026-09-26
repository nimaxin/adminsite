import json
from dataclasses import dataclass, field
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.i18n import gettext as _

# Longer than this a document is cut short in a list cell.
CELL_LENGTH = 120

# A document laid out over more lines than this starts with its inner objects
# and lists folded on the record page, so the page opens on its outline.
FOLD_LINES = 60


@dataclass
class Part:
    """One piece of a document as the record page lays it out.

    An object or a list holds its `children` and can fold; anything else is
    a single value, written as JSON in `text`.
    """

    kind: str
    name: str = ""
    text: str = ""
    children: list["Part"] = field(default_factory=list)
    last: bool = True
    folded: bool = False
    count: str = ""


class JSONField(Field):
    """A JSON column: an object, a list, or any other JSON document.

    Chosen for JSON columns by itself. The form edits the document in a code
    box that colours it and says where a mistake is while it is typed; the
    record page lays it out, each object and list folding on its line.
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
        return self.laid_out(value)

    def laid_out(self, value: Any) -> str:
        """The document as JSON text over several lines, as it is copied."""
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)

    def to_python(self, text: str) -> Any:
        """Read the text as a JSON document, or say where it stops being one."""
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise FieldValidationError(
                self.name,
                _(
                    "Line {line}, column {column}: this is not valid JSON.",
                    line=error.lineno,
                    column=error.colno,
                ),
            ) from None

    def outline(self, value: Any) -> Part:
        """The document laid out for reading, each object and list able to fold."""
        long = self.laid_out(value).count("\n") >= FOLD_LINES
        return self._part(value, None, depth=0, last=True, long=long)

    def _part(
        self, value: Any, name: str | None, *, depth: int, last: bool, long: bool
    ) -> Part:
        written = json.dumps(name, ensure_ascii=False) if name is not None else ""
        if isinstance(value, dict | list):
            kind = "object" if isinstance(value, dict) else "list"
            entries = (
                list(value.items())
                if isinstance(value, dict)
                else [(None, item) for item in value]
            )
            if not entries:
                return Part(
                    kind, written, "{}" if kind == "object" else "[]", last=last
                )
            return Part(
                kind,
                written,
                children=[
                    self._part(
                        item,
                        key,
                        depth=depth + 1,
                        last=index == len(entries) - 1,
                        long=long,
                    )
                    for index, (key, item) in enumerate(entries)
                ],
                last=last,
                folded=long and depth >= 1,
                count=self._count(kind, len(entries)),
            )
        text = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            return Part("text", written, text, last=last)
        if value is None or isinstance(value, bool):
            return Part("word", written, text, last=last)
        return Part("number", written, text, last=last)

    def _count(self, kind: str, count: int) -> str:
        if kind == "object":
            if count == 1:
                return _("{count} key", count=count)
            return _("{count} keys", count=count)
        if count == 1:
            return _("{count} item", count=count)
        return _("{count} items", count=count)
