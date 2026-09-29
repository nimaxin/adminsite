from dataclasses import dataclass
from typing import Any, Generic, TypeAlias, TypeGuard, TypeVar

from sqlalchemy.orm import QueryableAttribute, RelationshipProperty

from adminsite.exceptions import AdminSiteError
from adminsite.query import Sort

V_co = TypeVar("V_co", covariant=True)


# eq=False: comparing two attributes builds a SQL condition rather than
# answering, so links and sorts are equal only to themselves.
@dataclass(frozen=True, eq=False)
class Link(Generic[V_co]):
    """A column of a related model, such as `Link(Order.customer, Customer.email)`.

    The relation leads to the model that owns the column. A longer way
    nests links: `Link(Order.customer, Link(Customer.address, Address.city))`.
    """

    relation: QueryableAttribute[Any]
    column: "QueryableAttribute[V_co] | Link[V_co]"

    def __repr__(self) -> str:
        return f"Link({describe(self.relation)}, {describe(self.column)})"


# A column named by its attribute, by a Link, or by its name as a string,
# such as Order.total, Link(Order.customer, Customer.email) or "total".
ColumnReference: TypeAlias = str | QueryableAttribute[Any] | Link[Any]


@dataclass(frozen=True, eq=False)
class Descending:
    """A column sorted from the highest value down, such as newest first."""

    column: ColumnReference

    def __repr__(self) -> str:
        return f"Descending({describe(self.column)})"


def is_column(value: object) -> TypeGuard[ColumnReference]:
    """Whether a value names a column: an attribute, a Link or a string."""
    return isinstance(value, str | QueryableAttribute | Link)


def describe(reference: object) -> str:
    """Write a column reference as it appears in code, for a message."""
    if isinstance(reference, str):
        return f'"{reference}"'
    if isinstance(reference, QueryableAttribute):
        return f"{_name_of(reference.class_)}.{reference.key}"
    return repr(reference)


def written_path(reference: ColumnReference) -> str:
    """The dotted path a reference names, before it is checked against a model.

    A field knows its name this way from the moment it is made; the view it
    joins checks that the path belongs to its model.
    """
    if isinstance(reference, str):
        return reference
    if isinstance(reference, Link):
        return f"{reference.relation.key}.{written_path(reference.column)}"
    return reference.key


def path_of(reference: ColumnReference, model: type[Any]) -> str:
    """The dotted path a column reference names, starting from the model.

    `Link(Order.customer, Customer.email)` in an order view is
    `customer.email`. A string is taken as the path it already is.
    """
    if isinstance(reference, str):
        return reference
    if isinstance(reference, Link):
        key = _key_of(reference.relation, model)
        return f"{key}.{path_of(reference.column, _target_of(reference))}"
    if isinstance(reference, QueryableAttribute):
        return _key_of(reference, model)
    raise AdminSiteError(
        f"{reference!r} is not a column. Name one by its attribute, such as "
        f"{model.__name__}.id, or by its name as a string."
    )


def sort_of(entry: "ColumnReference | Descending", model: type[Any]) -> Sort:
    """The sort an entry asks for: `Descending(...)` or `"-name"` sort down."""
    if isinstance(entry, Descending):
        return Sort(path_of(entry.column, model), descending=True)
    if isinstance(entry, str):
        return Sort.parse(entry)
    return Sort(path_of(entry, model))


def _key_of(attribute: QueryableAttribute[Any], model: type[Any]) -> str:
    """An attribute's name, once it is known to belong to the model."""
    owner = _owner_of(attribute)
    if not issubclass(model, owner):
        raise AdminSiteError(
            f"{describe(attribute)} is a column of {owner.__name__}, not of "
            f"{model.__name__}. For a column of a related model, write "
            f"Link({model.__name__}.<relation>, {describe(attribute)})."
        )
    return attribute.key


def _target_of(link: Link[Any]) -> type[Any]:
    """The model a link's relation leads to, which must own its column."""
    prop = link.relation.property
    if not isinstance(prop, RelationshipProperty):
        raise AdminSiteError(
            f"{link!r} starts from {describe(link.relation)}, which is a column. "
            "A link starts from a relationship."
        )
    target: type[Any] = prop.mapper.class_
    column = link.column
    if isinstance(column, QueryableAttribute):
        owner = _owner_of(column)
        if not issubclass(target, owner):
            raise AdminSiteError(
                f"{link!r}: {describe(link.relation)} leads to {target.__name__}, "
                f"and {describe(column)} is a column of {owner.__name__}."
            )
    return target


def _owner_of(attribute: QueryableAttribute[Any]) -> type[Any]:
    """The model an attribute belongs to, refusing one read from an alias."""
    owner = attribute.class_
    if not isinstance(owner, type):
        raise AdminSiteError(
            f"{describe(attribute)} belongs to an alias. Name the column on "
            "the model itself."
        )
    return owner


def _name_of(owner: object) -> str:
    """A model's name, or an alias's."""
    return str(getattr(owner, "__name__", owner))
