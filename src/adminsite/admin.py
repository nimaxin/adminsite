from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.session import Database, SessionSource
from adminsite.fields import FieldRegistry, default_registry
from adminsite.http import endpoints
from adminsite.http.templating import Templates
from adminsite.views import ModelView, ViewRegistry

STATIC_DIR = Path(__file__).parent / "static"


class Admin:
    """The admin itself: a small ASGI app you mount into your own.

    ```python
    admin = Admin(engine, title="Acme")
    admin.add_view(OrderView)
    app.mount("/admin", admin)
    ```
    """

    def __init__(
        self,
        source: Database | SessionSource,
        *,
        title: str = "Admin",
        views: Sequence[ModelView | type[ModelView]] = (),
        inspector: SQLAlchemyInspector | None = None,
        fields: FieldRegistry | None = None,
        template_dirs: Sequence[str | Path] = (),
    ) -> None:
        self.database = source if isinstance(source, Database) else Database(source)
        self.title = title
        self.inspector = inspector or SQLAlchemyInspector()
        self.fields = fields or default_registry
        self.views = ViewRegistry()
        self.templates = Templates(template_dirs)
        self._app: Starlette | None = None

        for view in views:
            self.add_view(view)

    def add_view(self, view: ModelView | type[ModelView]) -> ModelView:
        """Register a model with the admin."""
        built = view(self.inspector, self.fields) if isinstance(view, type) else view
        return self.views.add(built)

    @property
    def app(self) -> Starlette:
        """The Starlette app behind the admin, built once."""
        if self._app is None:
            self._app = Starlette(routes=self.routes())
        return self._app

    def routes(self) -> list[Route | Mount]:
        """Every route the admin answers."""
        # The fixed paths come first, so a record cannot be called "new".
        return [
            Route("/", self._handler(endpoints.index), name="index"),
            Mount(
                "/static",
                app=StaticFiles(directory=STATIC_DIR),
                name="static",
            ),
            Route("/{view}", self._handler(endpoints.list_records), name="list"),
            Route(
                "/{view}/new",
                self._handler(endpoints.create_form),
                methods=["GET"],
                name="create_form",
            ),
            Route(
                "/{view}/new",
                self._handler(endpoints.create_record),
                methods=["POST"],
                name="create",
            ),
            Route(
                "/{view}/export",
                self._handler(endpoints.export_records),
                name="export",
            ),
            Route(
                "/{view}/action/{name}",
                self._handler(endpoints.run_action),
                methods=["POST"],
                name="action",
            ),
            Route(
                "/{view}/lookup/{path}",
                self._handler(endpoints.lookup),
                name="lookup",
            ),
            Route(
                "/{view}/{key}",
                self._handler(endpoints.detail),
                name="detail",
            ),
            Route(
                "/{view}/{key}/edit",
                self._handler(endpoints.edit_form),
                methods=["GET"],
                name="edit_form",
            ),
            Route(
                "/{view}/{key}/edit",
                self._handler(endpoints.edit_record),
                methods=["POST"],
                name="edit",
            ),
            Route(
                "/{view}/{key}/delete",
                self._handler(endpoints.delete_record),
                methods=["POST"],
                name="delete",
            ),
        ]

    async def render(
        self,
        name: str,
        request: Request,
        context: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> Response:
        """Render one of the admin templates."""
        return await self.templates.render(name, request, self, context, status_code)

    def _handler(self, endpoint: Any) -> Any:
        return partial(endpoint, self)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Let the admin be mounted like any other ASGI app."""
        await self.app(scope, receive, send)
