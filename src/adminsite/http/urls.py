from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request

from adminsite.views import ModelView

QueryParams = Mapping[str, Any]

# The query values that say where in the list the page is.
PAGING_KEYS = frozenset({"page", "after", "before"})


class Urls:
    """Builds links inside the admin, wherever the admin is mounted."""

    def __init__(self, request: Request) -> None:
        root = request.scope.get("root_path", "")
        self.base = root.rstrip("/")
        self.request = request

    def index(self) -> str:
        """The front page of the admin."""
        return f"{self.base}/"

    def static(self, path: str) -> str:
        """A file that ships with the package."""
        return f"{self.base}/static/{path}"

    def list(self, view: ModelView, **params: Any) -> str:
        """The list page of a view."""
        return self._with(f"{self.base}/{view.name}", params)

    def create(self, view: ModelView) -> str:
        """The page for adding a record."""
        return f"{self.base}/{view.name}/new"

    def detail(self, view: ModelView, key: str) -> str:
        """The page showing one record."""
        return f"{self.base}/{view.name}/{key}"

    def edit(self, view: ModelView, key: str) -> str:
        """The page for changing one record."""
        return f"{self.base}/{view.name}/{key}/edit"

    def delete(self, view: ModelView, key: str) -> str:
        """Where a delete is posted."""
        return f"{self.base}/{view.name}/{key}/delete"

    def action(self, view: ModelView, name: str) -> str:
        """Where a bulk action is posted."""
        return f"{self.base}/{view.name}/action/{name}"

    def export(self, view: ModelView, **params: Any) -> str:
        """Where the current list is exported."""
        return self._with(f"{self.base}/{view.name}/export", params)

    def save_view(self, view: ModelView) -> str:
        """Where the current list is saved under a name."""
        return f"{self.base}/{view.name}/saved-views"

    def delete_view(self, view: ModelView, key: int | None) -> str:
        """Where a saved view is removed."""
        return f"{self.base}/{view.name}/saved-views/{key}/delete"

    def lookup(self, view: ModelView, path: str) -> str:
        """Where a relation field searches for records."""
        return f"{self.base}/{view.name}/lookup/{path}"

    def palette(self) -> str:
        """Where the command palette looks things up."""
        return f"{self.base}/-/search"

    def activity(self, **params: Any) -> str:
        """The page listing recent changes across the admin."""
        return self._with(f"{self.base}/-/activity", params)

    def login(self) -> str:
        """The login page."""
        return f"{self.base}/login"

    def logout(self) -> str:
        """Where signing out is posted."""
        return f"{self.base}/logout"

    def here(self, **changes: Any) -> str:
        """This page again, with some query values changed or removed.

        A value of None drops the parameter, which is what a filter chip's
        remove button needs. Any change that is not about paging starts
        again from the first page.
        """
        paging = any(key in PAGING_KEYS for key in changes)
        params: list[tuple[str, str]] = [
            (key, value)
            for key, value in self.request.query_params.multi_items()
            if key not in changes and (paging or key not in PAGING_KEYS)
        ]
        for key, value in changes.items():
            if value is None:
                continue
            if isinstance(value, list | tuple):
                params.extend((key, str(item)) for item in value)
            else:
                params.append((key, str(value)))
        path = self.request.url.path
        return f"{path}?{urlencode(params)}" if params else path

    def sorted_by(self, path: str) -> str:
        """This page again, sorted by a column, turning it around if set."""
        current = self.request.query_params.get("sort", "")
        wanted = f"-{path}" if current == path else path
        return self.here(sort=wanted)

    def _with(self, path: str, params: QueryParams) -> str:
        pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, list | tuple):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        return f"{path}?{urlencode(pairs)}" if pairs else path


def sort_state(request: Request, path: str) -> str:
    """Whether a column is the one being sorted, and which way."""
    current = request.query_params.get("sort", "")
    if current == path:
        return "asc"
    if current == f"-{path}":
        return "desc"
    return ""


def keep_params(request: Request, drop: Sequence[str] = ()) -> str:
    """The current query string, without some of its parameters."""
    pairs = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in drop
    ]
    return urlencode(pairs)
