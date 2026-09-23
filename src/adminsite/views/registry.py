from collections.abc import Iterator, Sequence
from typing import Any

from adminsite.exceptions import AdminSiteError
from adminsite.views.model_view import ModelView


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
