from typing import TYPE_CHECKING

from adminsite.exceptions import renamed_names
from adminsite.fields.base import BaseField, Field
from adminsite.fields.choice import EnumField
from adminsite.fields.computed import ComputedField
from adminsite.fields.files import FileField, ImageField
from adminsite.fields.json_field import JSONField
from adminsite.fields.list_field import ListField
from adminsite.fields.password import PasswordField
from adminsite.fields.registry import (
    FieldRegistry,
    build_default_registry,
    default_registry,
)
from adminsite.fields.relation import RelationField
from adminsite.fields.scalars import (
    BooleanField,
    DecimalField,
    EmailField,
    FloatField,
    IntegerField,
    StringField,
    TextAreaField,
    UUIDField,
)
from adminsite.fields.temporal import DateField, DateTimeField, TimeField

__all__ = [
    "BaseField",
    "BooleanField",
    "ComputedField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "EmailField",
    "EnumField",
    "Field",
    "FieldRegistry",
    "FileField",
    "FloatField",
    "ImageField",
    "IntegerField",
    "JSONField",
    "ListField",
    "PasswordField",
    "RelationField",
    "StringField",
    "TextAreaField",
    "TimeField",
    "UUIDField",
    "build_default_registry",
    "default_registry",
]


if not TYPE_CHECKING:
    # The names 0.1.0a10 changed, refused with the name each has now. Hidden
    # from type checkers, which report an old name as missing.
    __getattr__ = renamed_names(
        __name__,
        {
            "ChoiceField": "EnumField",
            "Computed": "ComputedField",
            "FieldOptions": "Field",
            "TextField": "TextAreaField",
        },
    )
