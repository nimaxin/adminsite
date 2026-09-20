from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.strategy_options import _AbstractLoad

from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector

RelationTree = dict[str, "RelationTree"]


def build_relation_tree(
    inspector: SQLAlchemyInspector, model: type[Any], paths: Iterable[str]
) -> RelationTree:
    """Collect the relationships the given paths walk through."""
    tree: RelationTree = {}
    for path in paths:
        resolved = inspector.resolve(model, path)
        branch = tree
        for relation in resolved.relations:
            branch = branch.setdefault(relation.name, {})
    return tree


def build_load_options(
    inspector: SQLAlchemyInspector, model: type[Any], paths: Iterable[str]
) -> list[_AbstractLoad]:
    """Work out the eager loads that keep a page down to a few queries.

    A path such as `customer.email` loads the customer with the row, and a
    path through a collection such as `items.product` loads the items in a
    second query rather than multiplying the rows.
    """
    tree = build_relation_tree(inspector, model, paths)
    return _options_for(inspector, model, tree)


def _options_for(
    inspector: SQLAlchemyInspector, model: type[Any], tree: RelationTree
) -> list[_AbstractLoad]:
    options: list[_AbstractLoad] = []
    schema = inspector.inspect(model)

    for name, children in tree.items():
        relation = schema.relations[name]
        attribute = getattr(model, name)
        loader: _AbstractLoad = (
            selectinload(attribute) if relation.collection else joinedload(attribute)
        )
        if children:
            loader = loader.options(*_options_for(inspector, relation.target, children))
        options.append(loader)

    return options
