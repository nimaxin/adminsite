"""The overview: the dashboard cards this user may see."""

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

from adminsite.dashboard import load_dashboard

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "index",
]


async def index(admin: "Admin", request: Request) -> Response:
    """The overview: the dashboard cards this user may see."""
    cards = await load_dashboard(admin, request, admin.dashboard)
    return await admin.render("index.html", request, {"cards": cards, "view": None})
