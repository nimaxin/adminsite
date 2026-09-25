from typing import Any

from sqlalchemy import ARRAY, Column, Enum
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.inspection import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Mapper, RelationshipProperty
from sqlalchemy.orm.interfaces import MANYTOMANY, MANYTOONE
from sqlalchemy.types import TypeEngine

from adminsite.exceptions import (
    InvalidPathError,
    NotAModelError,
    UnknownFieldError,
)
from adminsite.schema import (
    FieldPath,
    FieldSchema,
    ModelSchema,
    RelationDirection,
    RelationSchema,
)
from adminsite.text import humanize, humanize_class, pluralize, snake_case


class SQLAlchemyInspector:
    """Describes SQLAlchemy models, the way `ModelInspector` asks for."""

    def __init__(self) -> None:
        self._cache: dict[type[Any], ModelSchema] = {}

    def inspect(self, model: type[Any]) -> ModelSchema:
        """Describe the model, reading its mapper only once per model."""
        cached = self._cache.get(model)
        if cached is not None:
            return cached

        mapper = self._mapper_of(model)
        label = humanize_class(model.__name__)
        schema = ModelSchema(
            model=model,
            name=snake_case(model.__name__),
            label=label,
            label_plural=pluralize(label),
            primary_key=tuple(
                mapper.get_property_by_column(column).key
                for column in mapper.primary_key
            ),
            fields=self._read_fields(mapper),
            relations=self._read_relations(mapper),
        )
        self._cache[model] = schema
        return schema

    def resolve(self, model: type[Any], path: str) -> FieldPath:
        """Walk a dotted path, collecting the relationships it goes through."""
        if not path:
            raise InvalidPathError(path, "the path is empty.")

        current = model
        relations: list[RelationSchema] = []
        names = path.split(".")

        for position, name in enumerate(names):
            schema = self.inspect(current)
            is_last = position == len(names) - 1

            if name in schema.fields:
                if not is_last:
                    rest = ".".join(names[position + 1 :])
                    raise InvalidPathError(
                        path, f"{name!r} is a field, so {rest!r} cannot follow it."
                    )
                return FieldPath(
                    source=model,
                    relations=tuple(relations),
                    field=schema.fields[name],
                )

            if name not in schema.relations:
                raise UnknownFieldError(current, name, path)

            relation = schema.relations[name]
            relations.append(relation)
            current = relation.target

        return FieldPath(source=model, relations=tuple(relations))

    def _mapper_of(self, model: type[Any]) -> Mapper[Any]:
        try:
            mapper = sqlalchemy_inspect(model)
        except NoInspectionAvailable:
            raise NotAModelError(model) from None
        if not isinstance(mapper, Mapper):
            raise NotAModelError(model)
        return mapper

    def _read_fields(self, mapper: Mapper[Any]) -> dict[str, FieldSchema]:
        fields: dict[str, FieldSchema] = {}
        for attribute in mapper.column_attrs:
            column = attribute.columns[0]
            if not isinstance(column, Column):
                continue
            fields[attribute.key] = self._read_field(attribute.key, column)
        return fields

    def _read_field(self, name: str, column: Column[Any]) -> FieldSchema:
        return FieldSchema(
            name=name,
            label=humanize(name.removesuffix("_id")),
            python_type=self._python_type_of(column.type),
            nullable=bool(column.nullable),
            primary_key=bool(column.primary_key),
            foreign_key=bool(column.foreign_keys),
            has_default=column.default is not None
            or column.server_default is not None
            or bool(column.primary_key and column.autoincrement is not False),
            max_length=getattr(column.type, "length", None),
            enum_values=self._enum_values_of(column.type),
            item=self._read_item(name, column.type),
        )

    def _read_item(self, name: str, column_type: TypeEngine[Any]) -> FieldSchema | None:
        """What each value of an array column is, or None for another column.

        An array of arrays is left as a document, since one value per line
        cannot hold it.
        """
        if not isinstance(column_type, ARRAY) or (column_type.dimensions or 1) > 1:
            return None
        item_type = column_type.item_type
        return FieldSchema(
            name=name,
            label=humanize(name),
            python_type=self._python_type_of(item_type),
            max_length=getattr(item_type, "length", None),
            enum_values=self._enum_values_of(item_type),
        )

    def _enum_values_of(self, column_type: TypeEngine[Any]) -> tuple[str, ...] | None:
        return tuple(column_type.enums) if isinstance(column_type, Enum) else None

    def _python_type_of(self, column_type: TypeEngine[Any]) -> type[Any]:
        try:
            return column_type.python_type
        except NotImplementedError:
            # Custom types may not name a python type. Treat them as text so
            # the field still renders, and let a field override fix it.
            return str

    def _read_relations(self, mapper: Mapper[Any]) -> dict[str, RelationSchema]:
        relations: dict[str, RelationSchema] = {}
        for relationship in mapper.relationships:
            relations[relationship.key] = self._read_relation(relationship)
        return relations

    def _read_relation(self, relationship: RelationshipProperty[Any]) -> RelationSchema:
        local_columns = tuple(
            column.key
            for column in relationship.local_columns
            if column.key is not None
        )
        return RelationSchema(
            name=relationship.key,
            label=humanize(relationship.key),
            target=relationship.mapper.class_,
            direction=self._direction_of(relationship),
            local_columns=local_columns,
            nullable=all(column.nullable for column in relationship.local_columns),
        )

    def _direction_of(
        self, relationship: RelationshipProperty[Any]
    ) -> RelationDirection:
        if relationship.direction is MANYTOMANY:
            return RelationDirection.MANY_TO_MANY
        if relationship.direction is MANYTOONE:
            return RelationDirection.MANY_TO_ONE
        if relationship.uselist:
            return RelationDirection.ONE_TO_MANY
        return RelationDirection.ONE_TO_ONE
