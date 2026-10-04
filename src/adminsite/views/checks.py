"""The checks a view's settings get when the admin starts, each with its message."""

from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from typing import Any, Literal, TypeAlias

from adminsite._text import template_names
from adminsite.columns import describe
from adminsite.exceptions import AdminSiteError, InvalidPathError, UnknownFieldError
from adminsite.fields import BaseField, Field, FieldRegistry, RelationField
from adminsite.inspector import SQLAlchemyInspector
from adminsite.permissions import RequestAction
from adminsite.schema import FieldPath, FieldSchema, ModelSchema, RelationDirection

__all__ = [
    "Takes",
    "check_excluded",
    "check_kind",
    "check_link_title",
    "check_list_flags",
    "check_options_used",
    "check_path",
    "check_placed",
    "check_title",
]

# What a setting may name: any field of the view, a column or relationship
# of the model, a column, a column the list can be sorted by, or a column of
# the model itself.
Takes: TypeAlias = Literal["fields", "paths", "columns", "sortable", "own columns"]


def check_path(
    view: str,
    setting: str,
    path: str,
    model: type[Any],
    takes: Takes,
    *,
    own: Mapping[str, BaseField],
    inspector: SQLAlchemyInspector,
) -> None:
    """Refuse a path the setting cannot take, saying what it can.

    A type checker sees an attribute; only this sees a string, and what
    the setting does with the path, such as sorting through a
    relationship holding many records, which no query can. `own` holds
    the view's own fields when the path starts from the view's model.
    """
    if path in own:
        if takes == "fields":
            return
        wanted = "columns and relationships" if takes == "paths" else "columns"
        raise AdminSiteError(
            f"{view}.{setting}: {own[path]!r} is a field of the view, not a "
            f"column of {model.__name__}, and {setting} takes {wanted}."
        )
    try:
        resolved = inspector.resolve(model, path)
    except UnknownFieldError as error:
        listed = own if takes == "fields" else {}
        raise AdminSiteError(
            f"{view}.{setting}: {_missing(path, error, listed, inspector)}"
        ) from None
    except InvalidPathError as error:
        raise AdminSiteError(f"{view}.{setting}: {error}") from None
    if takes in ("columns", "sortable") and resolved.field is None:
        target = inspector.inspect(resolved.relations[-1].target)
        texts = [
            name for name, found in target.fields.items() if found.python_type is str
        ]
        example = f"{path}.{(texts or list(target.fields))[0]}"
        raise AdminSiteError(
            f"{view}.{setting}: {describe(path)} is a relationship, and "
            f"{setting} takes columns. Name a column of "
            f"{target.model.__name__}, such as {describe(example)}."
        )
    if takes == "sortable" and resolved.crosses_collection:
        raise AdminSiteError(
            f"{view}.{setting}: {describe(path)} goes through a relationship "
            "holding many records, so no list can be sorted by it."
        )
    if takes == "own columns" and (resolved.relations or resolved.field is None):
        raise AdminSiteError(
            f"{view}.{setting}: {describe(path)} is not a column of "
            f"{model.__name__} itself, and {setting} takes the model's own "
            "columns."
        )


def check_title(
    where: str, template: str, model: type[Any], inspector: SQLAlchemyInspector
) -> None:
    """Refuse a record title that reads an attribute the model does not have.

    A template `str.format` cannot read would fail on every page that
    names a record, and a name the model lacks would show as nothing, so
    both stop the admin. So does a relationship: the pages load a record
    without its links, and loading them would cost every page naming one.
    """
    try:
        names = template_names(template)
    except ValueError as error:
        raise AdminSiteError(
            f"{where} cannot be read: {error}. Write each column's name in "
            "braces, such as {id}, and double a brace meant as text."
        ) from None
    schema = inspector.inspect(model)
    for name in names:
        if not hasattr(model, name):
            said = _missing(name, UnknownFieldError(model, name), {}, inspector)
            raise AdminSiteError(f"{where} reads {{{name}}}, and {said}")
        if name in schema.relations:
            raise AdminSiteError(
                f"{where} reads through the relationship {describe(name)}. A "
                f"record_title reads {model.__name__}'s own columns, since "
                "every page naming a record would otherwise load its links "
                f"too. Its columns: {', '.join(schema.fields)}."
            )


def check_link_title(
    item: BaseField, written: str, inspector: SQLAlchemyInspector
) -> None:
    """Refuse a link's record_title that reads what its model lacks."""
    if isinstance(item, RelationField) and item.record_title:
        check_title(
            f"{written}: its record_title {describe(item.record_title)}",
            item.record_title,
            item.related_model,
            inspector,
        )


def check_kind(
    given: Field[Any],
    column: FieldSchema,
    resolved: FieldPath,
    model: type[Any],
    *,
    registry: FieldRegistry,
    inspector: SQLAlchemyInspector,
) -> None:
    """Refuse a kind of field its column cannot hold.

    A type checker refuses the kind for a column named by its attribute,
    but not for one named by a string, nor RelationField on any column.
    A column of a type of the project's own, such as a TypeDecorator,
    names no python type to check the kind against.
    """
    if isinstance(given, RelationField):
        instead = _relationship_instead(given, column, resolved, model, inspector)
        raise AdminSiteError(
            f"{given!r} names a column, and RelationField shows a "
            f"relationship. {instead}"
        )
    if not isinstance(given.column, str) or not column.python_type_known:
        return
    held = column.python_type
    wanted = type(given).column_types
    # The kind the registry picks for the column always fits it, as a
    # kind registered for a type of the project's own does.
    picked = registry.field_class_for(column)
    if (
        not wanted
        or isinstance(given, picked)
        or not isinstance(held, type)
        or issubclass(held, wanted)
    ):
        return
    owner = resolved.relations[-1].target if resolved.relations else model
    kinds = " or ".join(kind.__name__ for kind in wanted)
    raise AdminSiteError(
        f"{given!r} is for {kinds} values, and {owner.__name__}.{column.name} "
        f"holds {held.__name__}. Use {picked.__name__}, or name the column "
        "without a field."
    )


def check_options_used(given: BaseField, completed: BaseField) -> None:
    """Refuse an option written on a field that its kind never reads.

    Every kind takes the options on BaseField, so a type checker lets
    max_length= onto a date field.
    """
    defaults = {item.name: item.default for item in dataclass_fields(completed)}
    for option in sorted(completed.unused_options):
        written = getattr(given, option)
        if written == defaults[option]:
            continue
        named = repr(given)
        if type(given) is not type(completed):
            named += f" becomes {type(completed).__name__}, which"
        raise AdminSiteError(
            f"{named} has no use for {option}={written!r}. Leave it out."
        )


def check_excluded(
    view: str,
    excluded: Mapping[RequestAction, Sequence[str]],
    shown: Sequence[str],
    schema: ModelSchema,
) -> None:
    """Refuse an exclude list naming a field the view does not show.

    Leaving such a field off a page does nothing. The usual case is a
    foreign key, such as `customer_id`, which the view shows as its
    relationship, `customer`.
    """
    for page, paths in excluded.items():
        for path in paths:
            if path in shown:
                continue
            said = f"{view}.exclude_fields_from_{page}"
            for relation in schema.relations.values():
                if (
                    relation.direction is RelationDirection.MANY_TO_ONE
                    and path in relation.local_columns
                    and relation.name in shown
                ):
                    raise AdminSiteError(
                        f"{said}: {describe(path)} is shown as its relationship "
                        f"{describe(relation.name)}. Name "
                        f"{describe(relation.name)}."
                    )
            raise AdminSiteError(
                f"{said}: {describe(path)} is not among the fields the view "
                "shows, so leaving it off does nothing. The view's fields: "
                f"{', '.join(shown)}."
            )


def check_placed(
    view: str, setting: str, path: str, fields: Sequence[str], placed: set[str]
) -> None:
    """Refuse a layout placing a field twice, or one the view does not show."""
    if path not in fields:
        raise AdminSiteError(
            f"{view}.{setting} places {path!r}, which is not one of the view's "
            "fields. Add it to fields, or take it out of the layout."
        )
    if path in placed:
        raise AdminSiteError(
            f"{view}.{setting} places {path!r} a second time. Each field goes in "
            "one place."
        )


def check_list_flags(
    item: BaseField, written: BaseField, *, excluded_by_list: bool
) -> None:
    """Refuse a field both hidden in the list and left off it.

    `written` is the field as the view wrote it, and `excluded_by_list`
    whether exclude_fields_from_list names it.
    """
    if not item.hidden_in_list:
        return
    if item.exclude_from_list:
        both = f"{written!r} has both hidden_in_list=True and exclude_from_list=True."
    elif excluded_by_list:
        both = (
            f"{written!r} has hidden_in_list=True, and exclude_fields_from_list "
            "names it."
        )
    else:
        return
    raise AdminSiteError(
        f"{both} hidden_in_list offers it among the columns people can add to "
        "the list; excluding it keeps it off the list altogether. Keep one."
    )


def _missing(
    path: str,
    error: UnknownFieldError,
    own: Mapping[str, BaseField],
    inspector: SQLAlchemyInspector,
) -> str:
    """Say which name does not exist, and list the names that do."""
    schema = inspector.inspect(error.model)
    said = (
        f"{error.model.__name__} has no column or relationship {describe(error.name)}."
    )
    if error.name != path:
        said = f"{describe(path)}: {said}"
    said += f" Its columns: {', '.join(schema.fields)}."
    if schema.relations:
        said += f" Its relationships: {', '.join(schema.relations)}."
    if own and error.name == path:
        said += f" The view's own fields: {', '.join(own)}."
    return said


def _relationship_instead(
    given: Field[Any],
    column: FieldSchema,
    resolved: FieldPath,
    model: type[Any],
    inspector: SQLAlchemyInspector,
) -> str:
    """What to write instead of RelationField on a column, for a message.

    A foreign key names the relationship it holds the key of.
    """
    owner = resolved.relations[-1].target if resolved.relations else model
    relations = inspector.inspect(owner).relations.values()
    for relation in relations:
        if (
            relation.direction is RelationDirection.MANY_TO_ONE
            and column.name in relation.local_columns
        ):
            if isinstance(given.column, str) or resolved.relations:
                through = [linked.name for linked in resolved.relations]
                written = describe(".".join([*through, relation.name]))
            else:
                written = f"{owner.__name__}.{relation.name}"
            return f"Write RelationField({written})."
    if not relations:
        return (
            f"{owner.__name__} has no relationship to show. Name the column "
            "without a field."
        )
    names = ", ".join(relation.name for relation in relations)
    return f"Name one of {owner.__name__}'s relationships: {names}."
