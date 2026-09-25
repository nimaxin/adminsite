from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from adminsite.exceptions import UnknownFieldError


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
    has_default: bool = False
    max_length: int | None = None
    enum_values: tuple[str, ...] | None = None
    # For a column holding a list of values, such as a Postgres ARRAY, what
    # each value is. None for any other column.
    item: "FieldSchema | None" = None

    @property
    def required(self) -> bool:
        """Whether a value has to be supplied when creating a record."""
        return not (self.nullable or self.has_default or self.primary_key)


@dataclass(frozen=True, slots=True)
class RelationSchema:
    """A link from one model to another."""

    name: str
    label: str
    target: type[Any]
    direction: RelationDirection
    local_columns: tuple[str, ...] = ()
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
