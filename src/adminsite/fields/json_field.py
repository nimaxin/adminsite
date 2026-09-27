import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.fields.documents import (
    DRAWN,
    Document,
    DocumentError,
    is_schema_function,
)
from adminsite.i18n import gettext as _

# Longer than this a document is cut short in a list cell.
CELL_LENGTH = 120

# A document laid out over more lines than this starts with its inner objects
# and lists folded on the record page, so the page opens on its outline.
FOLD_LINES = 60

# How many schemas a field whose schema comes from a function keeps read.
KEPT_SCHEMAS = 64


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

    Give it a `schema`, a Pydantic type or a JSON Schema dict, and the form
    is built from it instead: a switch for a yes or no, a number input that
    keeps to its limits, a picker for a few fixed values, rows for a list of
    objects. The document is checked against the schema when it is saved,
    by Pydantic where the schema is a Pydantic type, and the record page
    names each value by its title.

    ```python
    JSONField("settings", schema=ShopSettings)
    ```

    The schema can come from a function given the record, for a table whose
    rows hold documents of different shapes; it may return None where the
    record has no schema, and the form is then a code box. With
    `partial=True` each property may be left unset, and only those set are
    saved, as an override that changes a few settings keeps them.
    """

    widget = "json"
    python_type = dict
    error_message = 'Write valid JSON, such as {"key": "value"}.'

    def __init__(
        self,
        name: str,
        *,
        schema: Any = None,
        partial: bool = False,
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.schema = schema
        self.partial = partial
        self._documents: dict[int, Document] = {}
        if schema is not None and not is_schema_function(schema):
            # Read now, so a schema adminsite cannot read stops the admin
            # starting rather than the page that shows it.
            self.document_for(None)

    @property
    def schema_from_record(self) -> bool:
        """Whether the schema comes from a function given the record."""
        return is_schema_function(self.schema)

    def document_for(self, record: Any) -> Document | None:
        """The schema read for a record, or None where there is none."""
        if self.schema is None:
            return None
        schema = self.schema(record) if self.schema_from_record else self.schema
        if schema is None:
            return None
        known = self._documents.get(id(schema))
        if known is None or known.schema is not schema:
            if len(self._documents) >= KEPT_SCHEMAS:
                self._documents.clear()
            known = Document(schema, name=self.name, partial=self.partial)
            self._documents[id(schema)] = known
        return known

    def form_document(self, record: Any, value: Any) -> Document | None:
        """The schema a form is drawn from, where the value fits it.

        A stored value the schema cannot hold, such as a list where it asks
        for an object, is edited in a code box instead, so none of it is lost.
        """
        document = self.document_for(record)
        if document is None or not document.drawn or not document.fits(value):
            return None
        return document

    def read_form(
        self, data: Mapping[str, Any], path: str, *, record: Any = None
    ) -> Any:
        """Read the document a form sent, as a form or as a code box's text.

        Raises DocumentError, with a message for each part of a form that
        needs another look.
        """
        document = self.document_for(record)
        if document is not None and path + DRAWN in data:
            stored = getattr(record, path, None) if record is not None else None
            return document.read(data, path, stored=stored)
        raw = data.get(path)
        text = raw[-1] if isinstance(raw, list | tuple) and raw else raw
        value = self.parse(text if isinstance(text, str) else None)
        # A schema of the field's own was checked as the text was read.
        return self.checked(value, record) if self.schema_from_record else value

    def check(self, value: Any, record: Any = None) -> Any:
        """Check a whole document against the schema, as the API sends it.

        Returns it as the schema writes it, defaults filled in. Raises
        DocumentError, with a message for each part that needs another look.
        """
        document = self.document_for(record)
        if document is None or value is None:
            return value
        return document.check(value, self.name)

    def checked(self, value: Any, record: Any = None) -> Any:
        """Check a document written as text, with its problems in one message."""
        try:
            return self.check(value, record)
        except DocumentError as error:
            raise FieldValidationError(self.name, error.summary()) from None

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
        """Read the text as a JSON document, or say where it stops being one.

        Where the field has a schema of its own, the document is checked
        against it; one that comes from the record is checked by the form.
        """
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise FieldValidationError(
                self.name,
                _(
                    "Line {line}, column {column}: this is not valid JSON.",
                    line=error.lineno,
                    column=error.colno,
                ),
            ) from None
        return value if self.schema_from_record else self.checked(value)

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
