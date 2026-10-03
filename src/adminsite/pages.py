from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite.exceptions import AdminSiteError
from adminsite.i18n import gettext as _
from adminsite.text import snake_case

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "AdminPage",
]


class AdminPage:
    """A page of your own inside the admin, such as a report or a settings form.

    ```python
    from typing import Any


    class SalesReport(AdminPage):
        label = "Sales report"
        group = "Reports"
        template = "reports/sales.html"

        async def get_context(self, request: Request) -> dict[str, Any]:
            async with self.admin.database.session() as session:
                total = await session.scalar(select(func.sum(Order.total)))
            return {"total": total}
    ```

    The template lives in one of the admin's `template_dirs` and usually
    extends `adminsite/base.html`, so the page keeps the sidebar and header.
    It is served at `/-/sales_report` and listed in the sidebar and the
    command palette.
    """

    name: str = ""
    """The page's name in its URL, /-/ and the name.

    Left empty, the class's name in snake case, less a `_page` ending:
    `SalesReport` is served at `/-/sales_report`.
    """
    label: str = ""
    """The page's name in the sidebar. Left empty, the name in words."""
    group: str = ""
    """The sidebar section the page sits under, as a view's `group`."""
    icon: str = ""
    """The page's icon in the sidebar, as a view's `icon`."""
    template: str = ""
    """The template `get` renders, from one of the admin's `template_dirs`."""

    admin: "Admin"
    """The admin the page was added to, set when it is added."""

    def __init__(self) -> None:
        self._fill_names()

    def _fill_names(self) -> None:
        """Name the page after its class wherever no name was given."""
        # Outside __init__, so the Reference page shows each setting's own
        # default rather than this line.
        self.name = self.name or snake_case(type(self).__name__).removesuffix("_page")
        self.label = self.label or self.name.replace("_", " ").capitalize()

    async def allows(self, request: Request) -> bool:
        """Whether this user may open the page. Everyone may, unless you say.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            True when the user may open it. A page they may not is left
            out of the sidebar and answers 403.
        """
        return True

    async def get_context(self, request: Request) -> dict[str, Any]:
        """The values the template needs, on top of what every page has.

        Args:
            request: The request being answered.

        Returns:
            The values, by the names the template reads them by. The page
            itself is there as `page` too.
        """
        return {}

    async def get(self, request: Request) -> Response:
        """Show the page. Override it to answer with anything else.

        Args:
            request: The request being answered.

        Returns:
            The `template` rendered with `get_context`'s values.

        Raises:
            AdminSiteError: When the page has no `template`.
        """
        if not self.template:
            raise AdminSiteError(
                f"{type(self).__name__} needs a template, or its own get()."
            )
        context = {"page": self, **await self.get_context(request)}
        return await self.admin.render_template(self.template, request, context)

    async def post(self, request: Request, form: dict[str, Any]) -> Response:
        """Handle a form posted to the page, already checked for its token.

        Args:
            request: The request being answered.
            form: The submitted values, by input name.

        Returns:
            What to answer, such as a redirect back to the page. By default
            a 405, since a page takes no forms until it says so.
        """
        raise HTTPException(status_code=405, detail=_("This page takes no forms."))
