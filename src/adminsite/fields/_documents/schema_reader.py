"""A JSON Schema read into the shapes a form is drawn from."""

from collections.abc import Mapping
from typing import Any

from adminsite._text import humanize_class
from adminsite.fields._documents.inputs import (
    DocumentAddress,
    DocumentChoice,
    DocumentCode,
    DocumentDate,
    DocumentDateTime,
    DocumentDecimal,
    DocumentEmail,
    DocumentInteger,
    DocumentList,
    DocumentLongText,
    DocumentNumber,
    DocumentSwitch,
    DocumentText,
    DocumentTime,
    _Described,
)
from adminsite.fields._documents.shapes import (
    MISSING,
    Fixed,
    Group,
    Pairs,
    Property,
    Rows,
    Shape,
    Value,
)
from adminsite.fields.base import Field

__all__ = []


def _label_for(key: str, node: Mapping[str, Any]) -> str:
    """A property's label: its own title, or its name as adminsite writes it.

    Pydantic titles every property, Free Shipping Over, while adminsite
    writes labels as sentences, Free shipping over; a title that only
    repeats the name is written that way instead.
    """
    title = node.get("title")
    if not isinstance(title, str) or not title:
        return humanize_class(key)
    if title.lower() == key.replace("_", " ").lower():
        return humanize_class(key)
    return title


def _is_plain(shape: Shape) -> bool:
    """Whether a shape fits in one cell of a table."""
    if isinstance(shape, Fixed):
        return True
    return isinstance(shape, Value) and not isinstance(shape.field, DocumentCode)


class _SchemaReader:
    """Reads one JSON Schema into shapes, following its references."""

    def __init__(self, root: Mapping[str, Any]) -> None:
        self.root = root

    def pointed(self, reference: str) -> Mapping[str, Any] | None:
        """The part of the schema a reference such as #/$defs/Channel points at."""
        if not reference.startswith("#"):
            return None
        found: Any = self.root
        for part in reference[1:].split("/"):
            if not part:
                continue
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(found, Mapping) or part not in found:
                return None
            found = found[part]
        return found if isinstance(found, Mapping) else None

    def resolve(
        self, node: Mapping[str, Any], seen: frozenset[str]
    ) -> tuple[Mapping[str, Any] | None, bool, frozenset[str]]:
        """Follow references, and take null out of a choice.

        Returns the node, whether it may be null, and the references walked
        through, so a model that holds itself is not read for ever. A choice
        between shapes comes back marked, to be written as JSON.
        """
        nullable = False
        while True:
            rest = dict(node)
            if "$ref" in rest:
                reference = str(rest.pop("$ref"))
                target = self.pointed(reference)
                if reference in seen or target is None:
                    return None, nullable, seen
                seen = seen | {reference}
                node = {**target, **rest}
                continue
            if isinstance(rest.get("allOf"), list) and len(rest["allOf"]) == 1:
                only = rest.pop("allOf")[0]
                node = {**only, **rest}
                continue
            word = next(
                (one for one in ("anyOf", "oneOf") if isinstance(rest.get(one), list)),
                None,
            )
            if word is not None:
                options = rest.pop(word)
                real = [one for one in options if one.get("type") != "null"]
                nullable = nullable or len(real) < len(options)
                if len(real) == 1:
                    node = {**real[0], **rest}
                    continue
                if {one.get("type") for one in real} == {"number", "string"}:
                    # Pydantic's Decimal: a number, or text holding one.
                    return {**rest, "type": "number", "decimal": True}, nullable, seen
                return {**rest, "choice": real}, nullable, seen
            kind = rest.get("type")
            if isinstance(kind, list):
                kinds = [one for one in kind if one != "null"]
                nullable = nullable or len(kinds) < len(kind)
                rest["type"] = kinds[0] if len(kinds) == 1 else None
            return rest, nullable, seen

    def shape(
        self,
        node: Mapping[str, Any],
        *,
        key: str,
        label: str,
        description: str,
        required: bool,
        seen: frozenset[str] = frozenset(),
    ) -> tuple[Shape, bool]:
        """The shape of one part of a document, and whether it may be null."""
        resolved, nullable, seen = self.resolve(node, seen)
        settings: dict[str, Any] = {
            "label": label,
            "required": required and not nullable,
        }
        code = Value(_describe(DocumentCode(key, **settings), description))
        if resolved is None or "choice" in resolved:
            return code, nullable
        if "const" in resolved:
            return Fixed(resolved["const"]), nullable
        kind = resolved.get("type")
        found: Shape | None
        if kind == "object" or "properties" in resolved:
            found = self.object_shape(resolved, seen)
        elif kind == "array":
            found = self.array_shape(resolved, key, label, description, seen)
        else:
            single = self.value_field(resolved, key, settings)
            found = None if single is None else Value(_describe(single, description))
        return found or code, nullable

    def object_shape(
        self, node: Mapping[str, Any], seen: frozenset[str]
    ) -> Shape | None:
        """A group of named properties, or pairs of keys and values."""
        properties = node.get("properties")
        extra = node.get("additionalProperties")
        if isinstance(properties, Mapping) and properties:
            return Group(self.properties(node, seen), closed=extra is False)
        if not isinstance(extra, Mapping):
            return None
        resolved, _nullable, _walked = self.resolve(extra, seen)
        if resolved is None or "choice" in resolved:
            return None
        value = self.value_field(
            resolved, "value", {"label": "Value", "required": True}
        )
        if value is None:
            return None
        keys: tuple[Any, ...] = ()
        names = node.get("propertyNames")
        if isinstance(names, Mapping):
            named, _nullable, _walked = self.resolve(names, seen)
            if named is not None and isinstance(named.get("enum"), list):
                keys = tuple(named["enum"])
        key_field: Field[Any] = (
            DocumentChoice("key", options=keys, label="Key", required=True)
            if keys
            else DocumentText("key", label="Key", required=True)
        )
        return Pairs(key_field, value, keys)

    def properties(
        self, node: Mapping[str, Any], seen: frozenset[str]
    ) -> list[Property]:
        """Each property of an object, in the order the schema lists them."""
        required = set(node.get("required") or ())
        found = []
        for key, child in node["properties"].items():
            if not isinstance(child, Mapping):
                continue
            label = _label_for(key, child)
            description = str(child.get("description") or "")
            shape, nullable = self.shape(
                child,
                key=key,
                label=label,
                description=description,
                required=key in required,
                seen=seen,
            )
            found.append(
                Property(
                    key=key,
                    shape=shape,
                    label=label,
                    description=description,
                    required=key in required,
                    nullable=nullable,
                    default=child.get("default", MISSING),
                )
            )
        return found

    def array_shape(
        self,
        node: Mapping[str, Any],
        key: str,
        label: str,
        description: str,
        seen: frozenset[str],
    ) -> Shape | None:
        """Options to pick several of, rows of objects, or lines of values."""
        items = node.get("items")
        if "prefixItems" in node or not isinstance(items, Mapping):
            return None
        item, _nullable, walked = self.resolve(items, seen)
        if item is None or "choice" in item:
            return None
        if isinstance(item.get("enum"), list):
            choice = DocumentChoice(
                key, options=item["enum"], multiple=True, label=label
            )
            return Value(_describe(choice, description))
        if item.get("type") == "object" or "properties" in item:
            group = self.object_shape(item, walked)
            if not isinstance(group, Group):
                return None
            # A row is one line of a table, so each of its parts is one value.
            if not all(_is_plain(one.shape) for one in group.properties):
                return None
            return Rows(group)
        one = self.value_field(item, key, {"label": label})
        if one is None:
            return None
        return Value(_describe(DocumentList(key, item=one, label=label), description))

    def value_field(
        self, node: Mapping[str, Any], key: str, settings: dict[str, Any]
    ) -> Field[Any] | None:
        """The ordinary field that draws one plain value, if one fits."""
        if isinstance(node.get("enum"), list):
            return DocumentChoice(key, options=node["enum"], **settings)
        kind = node.get("type")
        if kind == "boolean":
            return DocumentSwitch(key, **{**settings, "required": False})
        if kind in ("integer", "number"):
            number: DocumentInteger | DocumentNumber | DocumentDecimal
            if kind == "integer":
                number = DocumentInteger(key, **settings)
            elif node.get("decimal"):
                number = DocumentDecimal(key, **settings)
            else:
                number = DocumentNumber(key, **settings)
            number.limit(node)
            return number
        if kind != "string":
            return None
        form = node.get("format")
        if form == "email":
            return DocumentEmail(key, **settings)
        if form == "date":
            return DocumentDate(key, **settings)
        if form == "date-time":
            return DocumentDateTime(key, **settings)
        if form == "time":
            return DocumentTime(key, **settings)
        text: DocumentText
        if form in ("uri", "url"):
            text = DocumentAddress(key, **settings)
        elif form in ("textarea", "multiline"):
            text = DocumentLongText(key, **settings)
        else:
            text = DocumentText(key, **settings)
        text.max_length = node.get("maxLength")
        text.min_length = node.get("minLength")
        text.pattern = node.get("pattern")
        return text


def _describe(item: Field[Any], description: str) -> Field[Any]:
    """Give a field its property's description, as the note under it."""
    if isinstance(item, _Described):
        item.description = description
    return item
