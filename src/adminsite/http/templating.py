from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import (
    BaseLoader,
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    select_autoescape,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse

from adminsite.http.urls import Urls, sort_state
from adminsite.i18n import direction, gettext, native_name
from adminsite.security.csrf import hidden_input

if TYPE_CHECKING:
    from adminsite.admin import Admin

TEMPLATE_ROOT = "adminsite"


class Templates:
    """Renders the admin pages, with room for a project to override one."""

    def __init__(self, extra_dirs: Sequence[str | Path] = ()) -> None:
        self.directories = [str(path) for path in extra_dirs]
        self.environment = Environment(
            loader=self._loader(),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
            enable_async=True,
        )
        self.environment.globals["sort_state"] = sort_state
        self.environment.globals["_"] = gettext

    def add_directory(self, directory: str | Path) -> None:
        """Look for templates in one more folder, after the ones given first."""
        self.directories.append(str(directory))
        self.environment.loader = self._loader()

    def _loader(self) -> ChoiceLoader:
        loaders: list[BaseLoader] = [
            FileSystemLoader(path) for path in self.directories
        ]
        loaders.append(PackageLoader("adminsite", "templates"))
        return ChoiceLoader(loaders)

    async def render(
        self,
        name: str,
        request: Request,
        admin: "Admin",
        context: dict[str, Any] | None = None,
        status_code: int = 200,
        *,
        own: bool = True,
    ) -> HTMLResponse:
        """Render a template with what every page needs already in place.

        The admin's own templates are named without their folder; a
        project's are named as they sit in its template dirs.
        """
        path = f"{TEMPLATE_ROOT}/{name}" if own else name
        language = request.scope.get("adminsite_language", admin.language)
        template = self.environment.get_template(path)
        values: dict[str, Any] = {
            "request": request,
            "admin": admin,
            "title": admin.title,
            "urls": Urls(request),
            # The sidebar leaves out what this user may not open.
            "groups": await navigation(admin, request),
            "show_activity": bool(await admin.history_views(request)),
            "user": request.scope.get("user_record"),
            "csrf_input": hidden_input(request),
            "messages": read_messages(request),
            "view": None,
            "language": language,
            "direction": direction(language),
            "languages": [(code, native_name(code)) for code in admin.languages],
        }
        values.update(context or {})
        values["current"] = current_key(values)
        body = await template.render_async(**values)
        return HTMLResponse(body, status_code=status_code)


@dataclass(frozen=True)
class NavItem:
    """One link in the sidebar."""

    key: str
    label: str
    url: str
    icon: str = ""


async def navigation(
    admin: "Admin", request: Request
) -> list[tuple[str, list[NavItem]]]:
    """The sidebar: views, then pages, by group in the order they were added."""
    urls = Urls(request)
    groups: dict[str, list[NavItem]] = {}
    for view in await admin.views_allowing(request):
        groups.setdefault(view.group, []).append(
            NavItem(f"view:{view.name}", view.label_plural, urls.list(view), view.icon)
        )
    for page in await admin.pages_allowing(request):
        groups.setdefault(page.group, []).append(
            NavItem(f"page:{page.name}", page.label, urls.page(page.name), page.icon)
        )
    return list(groups.items())


def current_key(context: dict[str, Any]) -> str:
    """Which sidebar link the page being shown belongs to."""
    view = context.get("view")
    if view is not None:
        return f"view:{view.name}"
    page = context.get("page")
    if page is not None and hasattr(page, "name"):
        return f"page:{page.name}"
    return ""


def read_messages(request: Request) -> list[dict[str, str]]:
    """Take the one time messages left by the last request."""
    session = request.scope.get("session")
    if not session:
        return []
    messages = session.pop("adminsite_messages", [])
    return list(messages)


def add_message(request: Request, text: str, kind: str = "info") -> None:
    """Leave a message for the page the user lands on next."""
    session = request.scope.get("session")
    if session is None:
        return
    # Assigned, not appended in place: the session is only saved when one of
    # its keys is set, so a change inside the list would be lost.
    waiting = list(session.get("adminsite_messages", []))
    session["adminsite_messages"] = [*waiting, {"text": text, "kind": kind}]
