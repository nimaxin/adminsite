from copy import copy
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from adminsite.columns import describe, path_of
from adminsite.exceptions import AdminSiteError
from adminsite.fields import BaseField, Field
from adminsite.permissions import RequestAction
from adminsite.schema import RelationDirection
from adminsite.views._checks import check_title
from adminsite.views._settings import default_paths
from adminsite.views.inlines import Inline

if TYPE_CHECKING:
    from adminsite.fields import FieldRegistry
    from adminsite.inspector import SQLAlchemyInspector
    from adminsite.schema import ModelSchema
    from adminsite.views._settings import SettingsReader
    from adminsite.views.model_view import ModelView

__all__ = [
    "InlineViews",
]


class InlineViews(dict[str, "ModelView[Any]"]):
    """The views that read and write a view's inline children, by inline name.

    Each is built when the view starts, so a mistake in an inline stops the
    admin starting. Asking for an inline the view does not have names the
    view, rather than raising a bare KeyError.
    """

    def __init__(
        self,
        view: "ModelView[Any]",
        base: "type[ModelView[Any]]",
        settings: "SettingsReader",
        schema: "ModelSchema",
        inspector: "SQLAlchemyInspector",
        registry: "FieldRegistry",
    ) -> None:
        super().__init__()
        self._view = view
        # The class each child view is built from, ModelView itself, handed
        # over since model_view imports this module for Inline.
        self._base = base
        self._settings = settings
        self._schema = schema
        self._inspector = inspector
        self._registry = registry
        for index, inline in enumerate(settings.entries("inlines", view.inlines)):
            self[inline.name] = self._build(inline, f"inlines[{index}]")

    def __missing__(self, name: str) -> "ModelView[Any]":
        raise AdminSiteError(
            f"{type(self._view).__name__} has no inline called {name!r}."
        )

    def _build(self, inline: Inline, setting: str) -> "ModelView[Any]":
        """Build the view that reads and writes one inline's children."""
        self._settings.converted(setting, inline.relation, path_of)
        self._settings.check(setting, inline.name, self._view.model, "paths")
        relation = self._schema.relation_named(inline.name)
        if not relation.collection:
            raise AdminSiteError(
                f"{type(self._view).__name__}.inlines names {inline.name!r}, which "
                "holds one record. An inline needs a relationship holding many."
            )
        if inline.record_title:
            check_title(
                f"{type(self._view).__name__}.{setting}.record_title: "
                f"{describe(inline.record_title)}",
                inline.record_title,
                relation.target,
                self._inspector,
            )
        # The relationship fills in the child's columns it joins on, and so
        # every link of the child made of them, the link back among them. None
        # of them appears in the child rows. Another link to the parent does.
        target = self._inspector.inspect(relation.target)
        joined = set(relation.remote_columns)
        filled = [
            *relation.remote_columns,
            *(
                found.name
                for found in target.relations.values()
                if found.direction is RelationDirection.MANY_TO_ONE
                and found.local_columns
                and set(found.local_columns) <= joined
            ),
        ]
        # Checked here, so a mistake names the parent's setting. The child view
        # takes the entries as they are, fields with their options included.
        entries = self._settings.entries(f"{setting}.fields", inline.fields)
        named = self._settings.paths(
            f"{setting}.fields",
            [
                entry.column if isinstance(entry, Field) else entry
                for entry in entries
                if not (isinstance(entry, Field) and entry.form_only)
            ],
            relation.target,
        )
        # Left off only where the rows show it, since an exclude list names
        # only fields the view shows.
        shown = named if entries else default_paths(target)
        filled = [name for name in filled if name in shown]

        def can_access_field(
            child: "ModelView[Any]",
            request: Request,
            field: BaseField,
            action: RequestAction,
        ) -> bool:
            # This view answers for its children, asked about each field by
            # its path from here, such as items.unit_price.
            asked = copy(field)
            asked.name = f"{inline.name}.{field.name}"
            return self._view.can_access_field(request, asked, action)

        namespace: dict[str, Any] = {
            "model": relation.target,
            "name": f"{self._view.name}__{inline.name}",
            "fields": list(entries),
            "exclude_fields_from_detail": filled,
            "exclude_fields_from_create": filled,
            "exclude_fields_from_edit": filled,
            "record_title": inline.record_title,
            "can_access_field": can_access_field,
        }
        child_class = type(
            f"{relation.target.__name__}Inline", (self._base,), namespace
        )
        try:
            built: ModelView[Any] = child_class(self._inspector, self._registry)
        except AdminSiteError as error:
            # The child's class is made here, so a mistake names the setting
            # it was written in rather than a class nobody wrote.
            said = str(error)
            generated = f"{child_class.__name__}."
            if said.startswith(generated):
                said = f".{said.removeprefix(generated)}"
            else:
                said = f": {said}"
            raise AdminSiteError(
                f"{type(self._view).__name__}.{setting}{said}"
            ) from None
        return built
