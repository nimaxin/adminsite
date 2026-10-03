from collections.abc import Iterator, Sequence
from typing import Any

from adminsite.exceptions import AdminSiteError
from adminsite.fields import RelationField
from adminsite.views.model_view import ModelView, view_class

__all__ = [
    "ViewRegistry",
]


class ViewRegistry:
    """Holds the views of one admin, in the order they were added."""

    def __init__(self) -> None:
        self._views: list[ModelView[Any]] = []
        self._by_name: dict[str, ModelView[Any]] = {}

    def add(self, view: ModelView[Any] | type[ModelView[Any]]) -> ModelView[Any]:
        """Register a view, given as its class, `ModelView[Tag]` or an instance."""
        built = view if isinstance(view, ModelView) else view_class(view)()
        if built.name in self._by_name:
            raise AdminSiteError(
                f"Two views are called {built.name!r}. "
                "Give one of them a different name."
            )
        built._views = self
        # The rows an inline edits name their links as this admin's views do.
        for inline in built.inlines:
            built._inline_views[inline.name]._views = self
        self._views.append(built)
        self._by_name[built.name] = built
        return built

    def get(self, name: str) -> ModelView[Any]:
        """Find a view by the name that appears in its URL."""
        try:
            return self._by_name[name]
        except KeyError:
            raise AdminSiteError(f"No view is called {name!r}.") from None

    def find(self, name: str) -> ModelView[Any] | None:
        """Find a view by name, or nothing if there is none."""
        return self._by_name.get(name)

    def for_model(self, model: type[Any]) -> ModelView[Any] | None:
        """The first view registered for a model, if there is one."""
        for view in self._views:
            if view.model is model:
                return view
        return None

    def all_for_model(self, model: type[Any]) -> list[ModelView[Any]]:
        """Every view registered for a model, in the order they were added."""
        return [view for view in self._views if view.model is model]

    def for_relation(self, item: RelationField) -> ModelView[Any] | None:
        """The view a relation's links and picker use.

        The one the field names with `view`, or else the first registered
        for its model.
        """
        target = item.related_model
        if item.view is None:
            return self.for_model(target)
        if isinstance(item.view, str):
            chosen = self._by_name.get(item.view)
            named = repr(item.view)
        else:
            chosen = self._of_class(item.view)
            named = item.view.__name__
        if chosen is None:
            raise AdminSiteError(
                f"The field {item.name!r} names the view {named}, which is not "
                f"registered with this admin. {self._views_of(target)}"
            )
        if chosen.model is not target:
            raise AdminSiteError(
                f"The field {item.name!r} names the view {named}, which "
                f"shows {chosen.model.__name__}, not {target.__name__}. "
                f"{self._views_of(target)}"
            )
        return chosen

    def _views_of(self, model: type[Any]) -> str:
        """List the views registered for a model, for a message."""
        found = [view.name for view in self.all_for_model(model)]
        if not found:
            return f"No view of {model.__name__} is registered."
        return f"The views of {model.__name__}: {', '.join(map(repr, found))}."

    def check_links(self) -> None:
        """Refuse a field whose `view` names a view this admin does not have.

        Run once every view is registered, since a view may name one that is
        added after it.
        """
        for view in self._views:
            owners = [(f"{type(view).__name__}.fields", view)] + [
                (
                    f"{type(view).__name__}.inlines[{index}].fields",
                    view._inline_views[inline.name],
                )
                for index, inline in enumerate(view.inlines)
            ]
            for setting, owner in owners:
                for path in owner._settings.placed:
                    item = owner._fields.field_for(path)
                    if not isinstance(item, RelationField) or item.view is None:
                        continue
                    try:
                        self.for_relation(item)
                    except AdminSiteError as error:
                        raise AdminSiteError(f"{setting}: {error}") from None

    def _of_class(self, wanted: type[ModelView[Any]]) -> ModelView[Any] | None:
        """The view registered as this class, if there is one."""
        for view in self._views:
            if type(view) is wanted:
                return view
        return None

    def grouped(
        self, only: Sequence[ModelView[Any]] | None = None
    ) -> list[tuple[str, list[ModelView[Any]]]]:
        """The views by sidebar group, keeping the order they were added."""
        groups: dict[str, list[ModelView[Any]]] = {}
        for view in self._views if only is None else only:
            groups.setdefault(view.group, []).append(view)
        return list(groups.items())

    @property
    def views(self) -> Sequence[ModelView[Any]]:
        """Every registered view."""
        return tuple(self._views)

    def __iter__(self) -> Iterator[ModelView[Any]]:
        return iter(self._views)

    def __len__(self) -> int:
        return len(self._views)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name
