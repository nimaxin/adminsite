from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from adminsite.fields.base import Field
from adminsite.fields.choice import ChoiceField
from adminsite.fields.json_field import JSONField
from adminsite.fields.list_field import ListField
from adminsite.fields.scalars import (
    BooleanField,
    DecimalField,
    FloatField,
    IntegerField,
    StringField,
    TextField,
    UUIDField,
)
from adminsite.fields.temporal import DateField, DateTimeField, TimeField
from adminsite.schema import FieldSchema

# Above this length a string is edited in a box instead of on one line.
TEXTAREA_LENGTH = 255


class FieldRegistry:
    """Decides which field type to use for a column."""

    def __init__(self) -> None:
        self._by_type: dict[type[Any], type[Field]] = {}

    def register(self, python_type: type[Any], field_class: type[Field]) -> None:
        """Use this field type for columns holding this Python type."""
        self._by_type[python_type] = field_class

    def field_class_for(self, schema: FieldSchema) -> type[Field]:
        """Return the field type that fits the column."""
        if schema.item is not None:
            return ListField
        if schema.enum_values:
            return ChoiceField

        python_type = schema.python_type
        if isinstance(python_type, type) and issubclass(python_type, Enum):
            return ChoiceField

        exact = self._by_type.get(python_type)
        if exact is not None:
            return self._narrow(exact, schema)

        for candidate, field_class in self._by_type.items():
            if isinstance(python_type, type) and issubclass(python_type, candidate):
                return self._narrow(field_class, schema)

        return StringField

    def build(self, schema: FieldSchema, **overrides: Any) -> Field:
        """Build the field for a column, ready to display and to parse."""
        field_class = self.field_class_for(schema)
        if schema.item is not None and issubclass(field_class, ListField):
            # Each value is read by the field its own type calls for.
            overrides.setdefault("item", self.build(schema.item))
        return field_class.from_schema(schema, **overrides)

    def _narrow(self, field_class: type[Field], schema: FieldSchema) -> type[Field]:
        if field_class is StringField and self._wants_a_box(schema):
            return TextField
        return field_class

    def _wants_a_box(self, schema: FieldSchema) -> bool:
        return schema.max_length is None or schema.max_length > TEXTAREA_LENGTH


def build_default_registry() -> FieldRegistry:
    """Build a registry holding the field types adminsite ships with."""
    registry = FieldRegistry()
    registry.register(dict, JSONField)
    registry.register(list, JSONField)
    registry.register(bool, BooleanField)
    registry.register(int, IntegerField)
    registry.register(float, FloatField)
    registry.register(Decimal, DecimalField)
    registry.register(str, StringField)
    registry.register(datetime, DateTimeField)
    registry.register(date, DateField)
    registry.register(time, TimeField)
    registry.register(UUID, UUIDField)
    return registry


default_registry = build_default_registry()
