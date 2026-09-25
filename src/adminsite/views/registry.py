from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

from adminsite.exceptions import AdminSiteError
from adminsite.views.model_view import ModelView

if TYPE_CHECKING:
    from adminsite.fields import RelationField


class ViewRegistry:
    """Holds the views of one admin, in the order they were added."""

    def __init__(self) -> None:
        self._views: list[ModelView] = []
        self._by_name: dict[str, ModelView] = {}

    def add(self, view: ModelView | type[ModelView]) -> ModelView:
        """Register a view, given either the class or an instance."""
        built = view() if isinstance(view, type) else view
        if built.name in self._by_name:
            raise AdminSiteError(
                f"Two views are called {built.name!r}. "
                "Give one of them a different name."
            )
        built.views = self
        # The rows an inline edits name their links as this admin's views do.
        for inline in built.inlines:
            built.inline_view(inline.name).views = self
        self._views.append(built)
        self._by_name[built.name] = built
        return built

    def get(self, name: str) -> ModelView:
        """Find a view by the name that appears in its URL."""
        try:
            return self._by_name[name]
        except KeyError:
            raise AdminSiteError(f"No view is called {name!r}.") from None

    def find(self, name: str) -> ModelView | None:
        """Find a view by name, or nothing if there is none."""
        return self._by_name.get(name)

    def for_model(self, model: type[Any]) -> ModelView | None:
        """The first view registered for a model, if there is one."""
        for view in self._views:
            if view.model is model:
                return view
        return None

    def all_for_model(self, model: type[Any]) -> list[ModelView]:
        """Every view registered for a model, in the order they were added."""
        return [view for view in self._views if view.model is model]

    def for_relation(self, item: "RelationField") -> ModelView | None:
        """The view a relation's links and picker use.

        The one the field names with `view`, or else the first registered
        for its model.
        """
        if item.view is None:
            return self.for_model(item.target)
        chosen = self._by_name.get(item.view)
        if chosen is None:
            raise AdminSiteError(
                f"The field {item.name!r} names the view {item.view!r}, "
                "and no view is called that."
            )
        if chosen.model is not item.target:
            raise AdminSiteError(
                f"The field {item.name!r} names the view {item.view!r}, which "
                f"shows {chosen.model.__name__}, not {item.target.__name__}."
            )
        return chosen

    def grouped(
        self, only: Sequence[ModelView] | None = None
    ) -> list[tuple[str, list[ModelView]]]:
        """The views by sidebar group, keeping the order they were added."""
        groups: dict[str, list[ModelView]] = {}
        for view in self._views if only is None else only:
            groups.setdefault(view.group, []).append(view)
        return list(groups.items())

    @property
    def views(self) -> Sequence[ModelView]:
        """Every registered view."""
        return tuple(self._views)

    def __iter__(self) -> Iterator[ModelView]:
        return iter(self._views)

    def __len__(self) -> int:
        return len(self._views)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name
