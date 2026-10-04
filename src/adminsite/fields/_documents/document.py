"""A document read from a form, checked, and filled into one."""

import copy
import dataclasses
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter, create_model
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from adminsite.exceptions import AdminSiteError, FieldValidationError
from adminsite.fields._documents.error_messages import message_for
from adminsite.fields._documents.inputs import DocumentCode, _python_value
from adminsite.fields._documents.schema_reader import _SchemaReader
from adminsite.fields._documents.shapes import (
    MISSING,
    SET,
    Fixed,
    Group,
    Pairs,
    Rows,
    Shape,
    Value,
    segment,
)
from adminsite.fields._documents.shown import Shown, _shown
from adminsite.fields.base import Field
from adminsite.fields.choices import EnumField
from adminsite.i18n import gettext as _

__all__ = [
    "Document",
    "DocumentError",
    "is_schema_function",
    "row_numbers",
]


def _as_text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, list | tuple):
        return str(raw[-1]) if raw else None
    return str(raw)


def _as_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list | tuple):
        return [str(one) for one in raw]
    return [str(raw)]


def _blank(raw: Any) -> bool:
    return all(not one.strip() for one in _as_list(raw))


class DocumentError(FieldValidationError):
    """A document that cannot be saved, with a message for each part.

    `errors` holds the messages by input name, `settings.free_shipping_over`,
    so each is shown beside its own input; `places` says where each input
    sits, Free shipping over, for a message about the whole document.
    """

    def __init__(
        self,
        field_name: str,
        errors: Mapping[str, str],
        places: Mapping[str, str] | None = None,
    ) -> None:
        self.errors = dict(errors)
        self.places = dict(places or {})
        first = next(iter(self.errors.values()), "")
        super().__init__(field_name, first)

    def summary(self) -> str:
        """Every problem in one message, each after the place it is in."""
        return " ".join(
            _("{place}: {problem}", place=self.places[name], problem=message)
            if self.places.get(name)
            else message
            for name, message in self.errors.items()
        )


@dataclass
class _Reading:
    """What reading a form turned up, besides the document itself."""

    errors: dict[str, str] = dataclasses.field(default_factory=dict)
    # For each list of rows, the input name of each item, in order, so a
    # problem Pydantic finds at a position lands on the right row.
    rows: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    # For each map, the input name of the row each key came from.
    keys: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)


def _read_value(item: Field[Any], raw: Any, name: str, reading: _Reading) -> Any:
    """Read one input: its value, None when empty, or MISSING when wrong."""
    try:
        if isinstance(item, EnumField) and item.multiple:
            return item.parse_many(_as_list(raw))
        return item.parse(_as_text(raw))
    except FieldValidationError as error:
        reading.errors[name] = error.message
        return MISSING


def row_numbers(data: Mapping[str, Any], name: str) -> list[int]:
    """The rows sent for a list or a map, by the marker each row carries."""
    pattern = re.compile(re.escape(name) + r"\.(\d+)")
    found = set()
    for key in data:
        matched = pattern.fullmatch(key)
        if matched:
            found.add(int(matched.group(1)))
    return sorted(found)


class Document:
    """A schema, read once, that fills, reads and checks documents."""

    def __init__(self, schema: Any, *, name: str = "", partial: bool = False) -> None:
        self.schema = schema
        self.partial = partial
        self.adapter: TypeAdapter[Any] | None = None
        if isinstance(schema, Mapping):
            self.json_schema: Mapping[str, Any] = schema
        else:
            try:
                self.adapter = TypeAdapter(schema)
                self.json_schema = self.adapter.json_schema()
            except Exception as error:
                raise AdminSiteError(
                    f"The field {name!r} has a schema adminsite cannot read, "
                    f"{schema!r}: {error}"
                ) from error
        self.shape, _nullable = _SchemaReader(self.json_schema).shape(
            self.json_schema, key=name, label="", description="", required=True
        )
        if partial and not isinstance(self.shape, Group):
            raise AdminSiteError(
                f"The field {name!r} is partial, which needs a schema for an "
                "object with named properties."
            )
        self._partial_adapter: TypeAdapter[Any] | None = None

    @property
    def drawn(self) -> bool:
        """Whether the form is more than a code box."""
        shape = self.shape
        return not (isinstance(shape, Value) and isinstance(shape.field, DocumentCode))

    def fits(self, value: Any) -> bool:
        """Whether a stored value can be put in the form without losing any of it."""
        if value is None:
            return True
        if isinstance(self.shape, Group | Pairs):
            return isinstance(value, dict)
        if isinstance(self.shape, Rows):
            return isinstance(value, list) and all(
                isinstance(one, dict) for one in value
            )
        return True

    # From a document to the form's inputs.

    def form_values(self, value: Any, name: str) -> dict[str, Any]:
        """The document as the form sends it, input name by input name.

        Filling the form from these, rather than from the document, means a
        form shown again after a failed save is drawn by the same code as one
        opened from the record. A property the document lacks starts at its
        default.
        """
        values: dict[str, Any] = {}
        self._flatten(
            self.shape, MISSING if value is None else value, name, values, True
        )
        return values

    def _flatten(
        self,
        shape: Shape,
        value: Any,
        name: str,
        values: dict[str, Any],
        top: bool = False,
    ) -> None:
        if isinstance(shape, Value):
            if value is MISSING or value is None:
                return
            item = shape.field
            if isinstance(item, EnumField) and item.multiple:
                values[name] = list(item.values_of(value))
            else:
                values[name] = item.serialize(_python_value(item, value))
            return
        if isinstance(shape, Group):
            given = value if isinstance(value, Mapping) else {}
            for found in shape.properties:
                child = f"{name}.{segment(found.key)}"
                if found.key in given:
                    one = given[found.key]
                    if top and self.partial:
                        values[child + SET] = ""
                else:
                    # A property left unset still starts at its default, for
                    # when it is set.
                    one = found.default
                self._flatten(found.shape, one, child, values)
            return
        if isinstance(shape, Rows):
            for index, item in enumerate(value if isinstance(value, list) else []):
                row = f"{name}.{index}"
                values[row] = ""
                given = item if isinstance(item, Mapping) else {}
                for column in shape.item.properties:
                    self._flatten(
                        column.shape,
                        given.get(column.key, column.default),
                        f"{row}.{segment(column.key)}",
                        values,
                    )
            return
        if isinstance(shape, Pairs):
            given = value if isinstance(value, Mapping) else {}
            for index, (key, one) in enumerate(given.items()):
                row = f"{name}.{index}"
                values[row] = ""
                values[f"{row}.key"] = shape.key.serialize(key)
                values[f"{row}.value"] = shape.value.serialize(
                    _python_value(shape.value, one)
                )

    # From the form's inputs to a document.

    def read(self, data: Mapping[str, Any], name: str, *, stored: Any = None) -> Any:
        """Read the document the form sent, and check it against the schema.

        Keys the stored document holds that the schema does not name are
        kept, unless the schema refuses them. Raises DocumentError, holding
        a message for each input that needs another look.
        """
        reading = _Reading()
        document = self._read(self.shape, data, name, reading, top=True)
        if document is MISSING:
            document = None
        document = _keep_unknown(self.shape, document, stored)
        try:
            checked = self._validate(document, name, reading)
        except DocumentError as error:
            # Checked even when a part could not be read, so every problem
            # shows at once; the part's own message comes first.
            for where, message in error.errors.items():
                reading.errors.setdefault(where, message)
        if reading.errors:
            raise DocumentError(name, reading.errors)
        return checked

    def _read(
        self,
        shape: Shape,
        data: Mapping[str, Any],
        name: str,
        reading: _Reading,
        *,
        top: bool = False,
    ) -> Any:
        if isinstance(shape, Fixed):
            return shape.value
        if isinstance(shape, Value):
            return _read_value(shape.field, data.get(name), name, reading)
        if isinstance(shape, Group):
            return self._read_group(shape, data, name, reading, top=top)
        if isinstance(shape, Rows):
            items, names = [], []
            for number in row_numbers(data, name):
                row = f"{name}.{number}"
                cells = [f"{row}.{segment(one.key)}" for one in shape.item.properties]
                if all(_blank(data.get(cell)) for cell in cells):
                    # A row added and left empty.
                    continue
                items.append(self._read_group(shape.item, data, row, reading))
                names.append(row)
            reading.rows[name] = names
            return items
        if isinstance(shape, Pairs):
            return self._read_pairs(shape, data, name, reading)
        return MISSING

    def _read_group(
        self,
        shape: Group,
        data: Mapping[str, Any],
        name: str,
        reading: _Reading,
        *,
        top: bool = False,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for found in shape.properties:
            child = f"{name}.{segment(found.key)}"
            if top and self.partial and child + SET not in data:
                continue
            one = self._read(found.shape, data, child, reading)
            if one is MISSING:
                continue
            if one is None:
                # Left empty: null where the schema asks for the key and
                # allows null, and otherwise left out, so a default applies.
                if found.required and found.nullable:
                    document[found.key] = None
                continue
            document[found.key] = one
        return document

    def _read_pairs(
        self, shape: Pairs, data: Mapping[str, Any], name: str, reading: _Reading
    ) -> dict[str, Any]:
        pairs: dict[str, Any] = {}
        rows: dict[str, str] = {}
        for number in row_numbers(data, name):
            row = f"{name}.{number}"
            key_raw, value_raw = data.get(f"{row}.key"), data.get(f"{row}.value")
            if _blank(key_raw) and _blank(value_raw):
                continue
            key = _read_value(shape.key, key_raw, f"{row}.key", reading)
            value = _read_value(shape.value, value_raw, f"{row}.value", reading)
            if key is MISSING:
                continue
            text = str(key)
            if text in rows:
                reading.errors[f"{row}.key"] = _("This key is given twice.")
                continue
            rows[text] = row
            if value is not MISSING:
                pairs[text] = value
        reading.keys[name] = rows
        return pairs

    # Checking a document against the schema.

    def check(self, document: Any, name: str) -> Any:
        """Check a whole document, as the API or a code box sends it."""
        return self._validate(document, name, _Reading())

    def _validate(self, document: Any, name: str, reading: _Reading) -> Any:
        adapter = self._checking_adapter()
        if adapter is None:
            # A JSON Schema written as a dict: each part's field has checked
            # what the form draws.
            return to_jsonable_python(document)
        try:
            checked = adapter.validate_python(document)
        except PydanticValidationError as error:
            errors: dict[str, str] = {}
            places: dict[str, str] = {}
            for found in error.errors():
                where, place, inside = self.where(found["loc"], name, reading)
                message = message_for(found)
                if inside:
                    # Inside a part written as JSON: say where in it.
                    message = _(
                        "{place}: {problem}",
                        place=".".join(str(one) for one in inside),
                        problem=message,
                    )
                errors.setdefault(where, message)
                places.setdefault(where, ", ".join(place))
            raise DocumentError(name, errors, places) from None
        return adapter.dump_python(
            checked, mode="json", by_alias=True, exclude_unset=self.partial
        )

    def _checking_adapter(self) -> TypeAdapter[Any] | None:
        if self.adapter is None or not self.partial:
            return self.adapter
        if self._partial_adapter is None:
            model = self.schema
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                # Only a model can be checked a few properties at a time; any
                # other type is checked by each part's own field.
                return None
            self._partial_adapter = TypeAdapter(_partial_model(model))
        return self._partial_adapter

    def where(
        self, location: Sequence[Any], name: str, reading: _Reading
    ) -> tuple[str, list[str], list[Any]]:
        """The input a problem at a place in the document belongs to.

        Returns its name, the labels of the parts it sits in, such as
        Channels, row 2, Id, and what is left of the place inside that input,
        as for a problem inside a part written as JSON.
        """
        shape = self.shape
        place: list[str] = []
        parts = list(location)
        while parts:
            part = parts[0]
            if isinstance(shape, Group):
                found = shape.find(str(part))
                if found is None:
                    break
                name = f"{name}.{segment(found.key)}"
                place.append(found.label)
                shape = found.shape
            elif isinstance(shape, Rows) and isinstance(part, int):
                rows = reading.rows.get(name)
                if rows is not None and part >= len(rows):
                    break
                name = rows[part] if rows is not None else f"{name}.{part}"
                place.append(_("row {number}", number=part + 1))
                shape = shape.item
            elif isinstance(shape, Pairs):
                place.append(str(part))
                parts.pop(0)
                row = reading.keys.get(name, {}).get(str(part))
                if row is not None:
                    name = f"{row}.key" if parts[:1] == ["[key]"] else f"{row}.value"
                return name, place, []
            else:
                break
            parts.pop(0)
        return name, place, parts

    # The record page.

    def shown(self, value: Any) -> "Shown":
        """The document as labelled values, for the record page.

        In a partial document, each field the document leaves out is marked
        unset, so the page can list it apart from what is set.
        """
        shown = _shown(self.shape, value, "")
        if self.partial and isinstance(self.shape, Group):
            given = value if isinstance(value, Mapping) else {}
            drawn = [
                found.key
                for found in self.shape.properties
                if not isinstance(found.shape, Fixed)
            ]
            for entry, key in zip(shown.entries, drawn, strict=True):
                entry.unset = key not in given
        return shown


def _partial_model(model: type[BaseModel]) -> type[BaseModel]:
    """The model with every property optional, its checks kept.

    A property left out is not checked, and not written back.
    """
    fields: dict[str, Any] = {}
    for key, info in model.model_fields.items():
        loose = copy.copy(info)
        loose.default = None
        loose.default_factory = None
        fields[key] = (info.annotation, loose)
    built: type[BaseModel] = create_model(
        f"Partial{model.__name__}", __base__=model, **fields
    )
    return built


def _keep_unknown(shape: Shape, document: Any, stored: Any) -> Any:
    """Put back the keys a stored document holds that the schema does not name."""
    if not isinstance(shape, Group) or not isinstance(document, dict):
        return document
    if not isinstance(stored, Mapping):
        return document
    for key, value in stored.items():
        found = shape.find(key)
        if found is None:
            if not shape.closed:
                document.setdefault(key, value)
        elif isinstance(found.shape, Group) and key in document:
            document[key] = _keep_unknown(found.shape, document[key], value)
    return document


def is_schema_function(source: Any) -> bool:
    """Whether a schema is a function that gives one for each record.

    A class is a schema itself, and so is a type such as list[int].
    """
    return (
        callable(source)
        and not isinstance(source, type | Mapping)
        and not hasattr(source, "__origin__")
    )
