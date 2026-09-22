from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite.exceptions import AdminSiteError
from adminsite.i18n import gettext as _
from adminsite.text import snake_case

if TYPE_CHECKING:
    from adminsite.admin import Admin


class AdminPage:
    """A page of your own inside the admin, such as a report or a settings form.

    ```python
    class SalesReport(AdminPage):
        label = "Sales report"
        group = "Reports"
        template = "reports/sales.html"

        async def get_context(self, request):
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
    label: str = ""
    group: str = ""
    template: str = ""

    # Set when the page is added to an admin.
    admin: "Admin"

    def __init__(self) -> None:
        self.name = self.name or snake_case(type(self).__name__).removesuffix("_page")
        self.label = self.label or self.name.replace("_", " ").capitalize()

    async def allows(self, request: Request) -> bool:
        """Whether this user may open the page. Everyone may, unless you say."""
        return True

    async def get_context(self, request: Request) -> dict[str, Any]:
        """The values the template needs, on top of what every page has."""
        return {}

    async def get(self, request: Request) -> Response:
        """Show the page. Override it to answer with anything else."""
        if not self.template:
            raise AdminSiteError(
                f"{type(self).__name__} needs a template, or its own get()."
            )
        context = {"page": self, **await self.get_context(request)}
        return await self.admin.render_template(self.template, request, context)

    async def post(self, request: Request, form: dict[str, Any]) -> Response:
        """Handle a form posted to the page, already checked for its token."""
        raise HTTPException(status_code=405, detail=_("This page takes no forms."))
