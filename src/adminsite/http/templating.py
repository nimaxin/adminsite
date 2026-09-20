from collections.abc import Sequence
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
from adminsite.security.csrf import hidden_input

if TYPE_CHECKING:
    from adminsite.admin import Admin

TEMPLATE_ROOT = "adminsite"


class Templates:
    """Renders the admin pages, with room for a project to override one."""

    def __init__(self, extra_dirs: Sequence[str | Path] = ()) -> None:
        loaders: list[BaseLoader] = [FileSystemLoader(str(path)) for path in extra_dirs]
        loaders.append(PackageLoader("adminsite", "templates"))
        self.environment = Environment(
            loader=ChoiceLoader(loaders),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
            enable_async=True,
        )
        self.environment.globals["sort_state"] = sort_state

    async def render(
        self,
        name: str,
        request: Request,
        admin: "Admin",
        context: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        """Render a template with what every page needs already in place."""
        template = self.environment.get_template(f"{TEMPLATE_ROOT}/{name}")
        values: dict[str, Any] = {
            "request": request,
            "admin": admin,
            "title": admin.title,
            "urls": Urls(request),
            "groups": admin.views.grouped(),
            "user": request.scope.get("user_record"),
            "csrf_input": hidden_input(request),
            "messages": read_messages(request),
            "view": None,
        }
        values.update(context or {})
        body = await template.render_async(**values)
        return HTMLResponse(body, status_code=status_code)


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
    session.setdefault("adminsite_messages", []).append({"text": text, "kind": kind})
