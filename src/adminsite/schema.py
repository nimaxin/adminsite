from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from adminsite.exceptions import UnknownFieldError

__all__ = [
    "FieldPath",
    "FieldSchema",
    "ModelSchema",
    "RelationDirection",
    "RelationSchema",
]


class RelationDirection(StrEnum):
    """How a relationship joins two models."""

    MANY_TO_ONE = "many_to_one"
    ONE_TO_MANY = "one_to_many"
    ONE_TO_ONE = "one_to_one"
    MANY_TO_MANY = "many_to_many"


@dataclass(frozen=True, slots=True)
class FieldSchema:
    """A single stored value on a model, described without any ORM detail."""

    name: str
    label: str
    python_type: type[Any]
    nullable: bool = False
    primary_key: bool = False
    foreign_key: bool = False
    # Whether the database numbers new records with it, as it does a
    # table's integer primary key.
    autoincrement: bool = False
    # Whether a new record gets a value without one being given: from a
    # default, a server default or the database's numbering.
    has_default: bool = False
    max_length: int | None = None
    # Whether a datetime column keeps the time zone, as one declared
    # DateTime(timezone=True) does.
    with_timezone: bool = False
    enum_values: tuple[str, ...] | None = None
    # The whole numbers an integer column holds, as its type declares them:
    # -2**31 up to 2**31 - 1 for an Integer. None for any other column.
    integer_range: range | None = None
    # For a column holding a list of values, such as a Postgres ARRAY, what
    # each value is. None for any other column.
    item: "FieldSchema | None" = None
    # False when the column's type is the project's own, such as a
    # TypeDecorator, or names no python type, so python_type is a guess.
    python_type_known: bool = True

    @property
    def required(self) -> bool:
        """Whether a value has to be supplied when creating a record."""
        return not (self.nullable or self.has_default)


@dataclass(frozen=True, slots=True)
class RelationSchema:
    """A link from one model to another."""

    name: str
    label: str
    target: type[Any]
    direction: RelationDirection
    # The columns it joins on, by their attribute names: its own model's,
    # and the target's. A many-to-many joins the target through a table of
    # its own, so it has no target columns.
    local_columns: tuple[str, ...] = ()
    remote_columns: tuple[str, ...] = ()
    nullable: bool = False

    @property
    def collection(self) -> bool:
        """Whether this side of the relationship holds many records."""
        return self.direction in (
            RelationDirection.ONE_TO_MANY,
            RelationDirection.MANY_TO_MANY,
        )


@dataclass(frozen=True, slots=True)
class ModelSchema:
    """Everything adminsite needs to know about one model."""

    model: type[Any]
    name: str
    label: str
    label_plural: str
    primary_key: tuple[str, ...]
    fields: Mapping[str, FieldSchema] = field(default_factory=dict)
    relations: Mapping[str, RelationSchema] = field(default_factory=dict)

    def field_named(self, name: str) -> FieldSchema:
        """Return the field, or raise if the model has no such field."""
        try:
            return self.fields[name]
        except KeyError:
            raise UnknownFieldError(self.model, name) from None

    def relation_named(self, name: str) -> RelationSchema:
        """Return the relationship, or raise if the model has no such link."""
        try:
            return self.relations[name]
        except KeyError:
            raise UnknownFieldError(self.model, name) from None

    def has(self, name: str) -> bool:
        """Whether the model has a field or relationship with this name."""
        return name in self.fields or name in self.relations

    def identity_of(self, record: Any) -> str:
        """Write the primary key of a record as one string, for a URL."""
        return ",".join(str(getattr(record, name)) for name in self.primary_key)

    def key_parts(self, key: Any) -> tuple[Any, ...]:
        """A key as its parts: "A,1" is ("A", "1") for a key of two columns."""
        if isinstance(key, tuple):
            return key
        if isinstance(key, str) and len(self.primary_key) > 1:
            return tuple(key.split(","))
        return (key,)


@dataclass(frozen=True, slots=True)
class FieldPath:
    """A resolved path such as `customer.email`, ready to read or to load."""

    source: type[Any]
    relations: tuple[RelationSchema, ...] = ()
    field: FieldSchema | None = None

    @property
    def parts(self) -> tuple[str, ...]:
        """The path split into names, in the order they are walked."""
        names = [relation.name for relation in self.relations]
        if self.field is not None:
            names.append(self.field.name)
        return tuple(names)

    @property
    def dotted(self) -> str:
        """The path as written, for example `customer.email`."""
        return ".".join(self.parts)

    @property
    def label(self) -> str:
        """The label of whatever the path points at."""
        if self.field is not None:
            return self.field.label
        return self.relations[-1].label if self.relations else ""

    @property
    def points_at_relation(self) -> bool:
        """Whether the path ends on a relationship instead of a field."""
        return self.field is None

    @property
    def crosses_collection(self) -> bool:
        """Whether the path passes through a relationship holding many rows."""
        return any(relation.collection for relation in self.relations)

    def __str__(self) -> str:
        return self.dotted
