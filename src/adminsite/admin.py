from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from adminsite.audit import AuditLog
from adminsite.auth import AuthProvider
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.session import Database, SessionSource
from adminsite.dashboard import ModelCounts, Widget
from adminsite.exceptions import AdminSiteError, PermissionDeniedError
from adminsite.fields import FieldRegistry, default_registry
from adminsite.http import api, endpoints
from adminsite.http.palette import palette
from adminsite.http.templating import Templates
from adminsite.http.urls import Urls
from adminsite.i18n import activate, negotiate
from adminsite.i18n import gettext as _
from adminsite.pages import AdminPage
from adminsite.plugins import Plugin
from adminsite.saved_views import SavedViews
from adminsite.security import Permission
from adminsite.security.csrf import TOKEN_HEADER, is_valid
from adminsite.text import snake_case
from adminsite.views import ModelView, ViewRegistry

STATIC_DIR = Path(__file__).parent / "static"

HEADINGS = {403: "Not allowed", 404: "Not found"}

# Paths under /-/ that the admin keeps for itself.
RESERVED_PAGES = frozenset({"activity", "api", "files", "language", "search", "static"})
LANGUAGE_COOKIE = "adminsite_language"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Endpoint = Callable[["Admin", Request], Awaitable[Response]]


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
        banner: str = "",
        views: Sequence[ModelView | type[ModelView]] = (),
        inspector: SQLAlchemyInspector | None = None,
        fields: FieldRegistry | None = None,
        template_dirs: Sequence[str | Path] = (),
        auth: AuthProvider | None = None,
        secret_key: str = "",
        audit: AuditLog | bool = False,
        saved_views: SavedViews | bool = False,
        session_cookie: str | None = None,
        pages: Sequence[AdminPage | type[AdminPage]] = (),
        plugins: Sequence[Plugin] = (),
        dashboard: Sequence[Widget] | None = None,
        api: bool = False,
        session_https_only: bool = False,
        session_max_age: int | None = 14 * 24 * 3600,
        language: str = "en",
        languages: Sequence[str] = (),
        translations: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        if auth is not None and not secret_key:
            raise AdminSiteError(
                "Signing in needs a secret_key to sign the session cookie."
            )
        self.database = source if isinstance(source, Database) else Database(source)
        self.title = title
        # One line above every page and the sign in page, such as a warning
        # that this copy is a staging one. Escaped, unless given as Html.
        self.banner = banner
        self.inspector = inspector or SQLAlchemyInspector()
        self.fields = fields or default_registry
        self.views = ViewRegistry()
        self.templates = Templates(template_dirs)
        self.auth = auth
        self.audit = AuditLog() if audit is True else (audit or None)
        self.saved_views = (
            SavedViews() if saved_views is True else (saved_views or None)
        )
        self.secret_key = secret_key
        # Named after the title, so two admins in one app keep separate
        # sessions without anyone having to think about it.
        self.session_cookie = session_cookie or f"adminsite_{snake_case(title)}"
        # Off by default so the admin works over plain HTTP while you build
        # it. Switch it on wherever the admin is served over HTTPS.
        self.session_https_only = session_https_only
        self.session_max_age = session_max_age
        self._app: Starlette | None = None
        self.pages: dict[str, AdminPage] = {}
        # The cards on the overview; the record counts unless you choose.
        self.dashboard: list[Widget] = (
            list(dashboard) if dashboard is not None else [ModelCounts()]
        )
        self.plugins: list[Plugin] = []
        self.stylesheets: list[str] = []
        self.scripts: list[str] = []
        self._extra_routes: list[Route | Mount] = []
        # The JSON API at /-/api, off unless asked for.
        self.api = api
        # The language the admin speaks, and the ones people may switch to.
        self.language = language
        self.languages = list(dict.fromkeys([language, *languages]))
        self.translations = dict(translations or {})

        for view in views:
            self.add_view(view)
        for page in pages:
            self.add_page(page)
        for plugin in plugins:
            self.use(plugin)

    def add_view(self, view: ModelView | type[ModelView]) -> ModelView:
        """Register a model with the admin."""
        built = view(self.inspector, self.fields) if isinstance(view, type) else view
        if built.audit is None:
            built.audit = self.audit
        return self.views.add(built)

    def add_page(self, page: AdminPage | type[AdminPage]) -> AdminPage:
        """Add a page of your own, served at /-/ and its name."""
        built = page() if isinstance(page, type) else page
        if built.name in RESERVED_PAGES or built.name in self.pages:
            raise AdminSiteError(f"A page is already called {built.name!r}.")
        built.admin = self
        self.pages[built.name] = built
        return built

    def use(self, plugin: Plugin) -> Plugin:
        """Let a plugin add what it brings."""
        plugin.setup(self)
        self.plugins.append(plugin)
        return plugin

    def add_route(
        self,
        path: str,
        endpoint: Endpoint,
        *,
        methods: Sequence[str] = ("GET",),
        name: str | None = None,
        guarded: bool = True,
    ) -> None:
        """Answer one more path, called as `endpoint(admin, request)`.

        The path has to start with /-/, which no model name can take, and
        the route sits behind the admin's sign in unless `guarded` is off.
        """
        if not path.startswith("/-/"):
            raise AdminSiteError(f"Extra routes live under /-/, not at {path!r}.")
        self._before_start("routes")
        handler = self._handler(endpoint, guarded)
        self._extra_routes.append(
            Route(path, handler, methods=list(methods), name=name)
        )

    def add_static(self, name: str, directory: str | Path) -> None:
        """Serve a folder of files at /-/static/ and the name."""
        self._before_start("static files")
        self._extra_routes.append(
            Mount(f"/-/static/{name}", app=StaticFiles(directory=directory))
        )

    def add_template_dir(self, directory: str | Path) -> None:
        """Look for templates in one more folder, before the built in ones."""
        self.templates.add_directory(directory)

    def add_stylesheet(self, href: str) -> None:
        """Load a stylesheet on every page. A relative path starts at the admin."""
        self.stylesheets.append(href)

    def add_script(self, src: str) -> None:
        """Load a script on every page. A relative path starts at the admin."""
        self.scripts.append(src)

    async def pages_allowing(self, request: Request) -> list[AdminPage]:
        """The pages of your own this user may open."""
        return [page for page in self.pages.values() if await page.allows(request)]

    def _before_start(self, what: str) -> None:
        if self._app is not None:
            raise AdminSiteError(
                f"Add {what} before the admin answers its first request."
            )

    async def views_allowing(
        self, request: Request, *permissions: Permission | str
    ) -> list[ModelView]:
        """The views where this user has every one of these permissions."""
        wanted = permissions or (Permission.VIEW,)
        found = []
        for view in self.views:
            for permission in wanted:
                if not await view.allows(permission, request=request):
                    break
            else:
                found.append(view)
        return found

    async def history_views(self, request: Request) -> list[ModelView]:
        """The views whose history this user may read, if auditing is on."""
        if self.audit is None:
            return []
        return await self.views_allowing(request, Permission.VIEW, Permission.HISTORY)

    @property
    def app(self) -> Starlette:
        """The Starlette app behind the admin, built once."""
        if self._app is None:
            self._app = Starlette(
                routes=self.routes(),
                middleware=self.middleware(),
                exception_handlers={
                    HTTPException: self._error_page,
                    PermissionDeniedError: self._error_page,
                },
            )
        return self._app

    def language_for(self, request: Request) -> str:
        """The language to answer in: the one chosen, else the browser's."""
        chosen = request.cookies.get(LANGUAGE_COOKIE, "")
        if chosen in self.languages:
            return chosen
        if len(self.languages) > 1:
            header = request.headers.get("accept-language", "")
            found = negotiate(header, self.languages)
            if found is not None:
                return found
        return self.language

    def speak(self, request: Request) -> str:
        """Answer this request in its language."""
        language = self.language_for(request)
        activate(language, self.translations)
        request.scope["adminsite_language"] = language
        return language

    async def _error_page(self, request: Request, error: Exception) -> Response:
        """Show a refusal or a missing page inside the admin, not as bare text."""
        self.speak(request)
        if isinstance(error, HTTPException):
            status, message = error.status_code, str(error.detail)
            headers = error.headers
        else:
            status, message, headers = 403, str(error), None
        if request.headers.get("hx-request") == "true" or status < 400:
            return PlainTextResponse(message, status_code=status, headers=headers)
        response = await self.render(
            "error.html",
            request,
            {
                "status": status,
                "heading": _(HEADINGS.get(status, "Something went wrong")),
                "message": message,
            },
            status_code=status,
        )
        if headers:
            response.headers.update(headers)
        return response

    def middleware(self) -> list[Middleware]:
        """The middleware the admin runs behind, if it needs any."""
        if not self.secret_key:
            return []
        return [
            Middleware(
                SessionMiddleware,
                secret_key=self.secret_key,
                session_cookie=self.session_cookie,
                same_site="lax",
                https_only=self.session_https_only,
                max_age=self.session_max_age,
            )
        ]

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
            Route(
                "/login",
                self._handler(endpoints.login_form, guarded=False),
                methods=["GET"],
                name="login_form",
            ),
            Route(
                "/login",
                self._handler(endpoints.login, guarded=False),
                methods=["POST"],
                name="login",
            ),
            Route(
                "/logout",
                self._handler(endpoints.logout, guarded=False),
                methods=["POST"],
                name="logout",
            ),
            # Pages that are not a model sit under /-/ so no model name can
            # ever collide with them.
            Route(
                "/-/language",
                self._handler(endpoints.choose_language, guarded=False),
                methods=["POST"],
                name="language",
            ),
            Route(
                "/-/search",
                self._handler(palette),
                name="palette",
            ),
            Route(
                "/-/activity",
                self._handler(endpoints.activity),
                name="activity",
            ),
            Route(
                "/-/files/{view}/{path}/{key:path}",
                self._handler(endpoints.stored_file),
                name="file",
            ),
            *self._api_routes(),
            *self._extra_routes,
            Route(
                "/-/{page}",
                self._handler(endpoints.custom_page),
                methods=["GET", "POST"],
                name="page",
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
                "/{view}/import",
                self._handler(endpoints.import_form),
                methods=["GET"],
                name="import_form",
            ),
            Route(
                "/{view}/import",
                self._handler(endpoints.import_preview),
                methods=["POST"],
                name="import_preview",
            ),
            Route(
                "/{view}/import/template",
                self._handler(endpoints.import_template),
                methods=["GET"],
                name="import_template",
            ),
            Route(
                "/{view}/import/{token}",
                self._handler(endpoints.import_records),
                methods=["POST"],
                name="import",
            ),
            Route(
                "/{view}/saved-views",
                self._handler(endpoints.save_list_view),
                methods=["POST"],
                name="save_view",
            ),
            Route(
                "/{view}/saved-views/{saved}/delete",
                self._handler(endpoints.delete_list_view),
                methods=["POST"],
                name="delete_view",
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

    def _api_routes(self) -> list[Route]:
        if not self.api:
            return []
        handler = self._api_handler
        return [
            Route("/-/api", handler(api.index), name="api"),
            Route(
                "/-/api/{view}",
                handler(api.collection),
                methods=["GET", "POST"],
                name="api_collection",
            ),
            Route(
                "/-/api/{view}/actions/{name}",
                handler(api.action),
                methods=["POST"],
                name="api_action",
            ),
            Route(
                "/-/api/{view}/{key}",
                handler(api.item),
                methods=["GET", "PATCH", "DELETE"],
                name="api_item",
            ),
        ]

    def _api_handler(self, endpoint: Any) -> Any:
        """Guard an API endpoint: JSON answers, and tokens as well as sessions."""

        async def handle(request: Request) -> Response:
            self.speak(request)
            try:
                by_token = False
                if self.auth is not None:
                    user, by_token = await self._api_user(request)
                    if user is None:
                        return JSONResponse(
                            {"error": _("Sign in first.")},
                            status_code=401,
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    request.scope["user_record"] = user
                # A browser sends the session cookie by itself, so a change
                # made with it has to carry the form token as a header.
                if request.method not in SAFE_METHODS and not by_token:
                    sent = request.headers.get(TOKEN_HEADER)
                    if not is_valid(request, sent):
                        return JSONResponse(
                            {
                                "error": _(
                                    "Send the {header} header.", header=TOKEN_HEADER
                                )
                            },
                            status_code=403,
                        )
                answer: Response = await endpoint(self, request)
                return answer
            except (api.ApiError, HTTPException, PermissionDeniedError) as error:
                return api.error_response(error)
            finally:
                await request.close()

        return handle

    async def _api_user(self, request: Request) -> tuple[Any, bool]:
        """Who is calling: a bearer token first, then the session."""
        assert self.auth is not None
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return await self.auth.authenticate_token(token.strip()), True
        return await self.auth.current_user(request), False

    async def render(
        self,
        name: str,
        request: Request,
        context: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> Response:
        """Render one of the admin templates."""
        return await self.templates.render(name, request, self, context, status_code)

    async def render_template(
        self,
        name: str,
        request: Request,
        context: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> Response:
        """Render a template of your own, found in the admin's template dirs."""
        return await self.templates.render(
            name, request, self, context, status_code, own=False
        )

    def _handler(self, endpoint: Any, guarded: bool = True) -> Any:
        checks_user = guarded and self.auth is not None

        async def handle(request: Request) -> Response:
            self.speak(request)
            try:
                if checks_user and self.auth is not None:
                    user = await self.auth.current_user(request)
                    if user is None:
                        return RedirectResponse(Urls(request).login(), status_code=303)
                    request.scope["user_record"] = user
                answer: Response = await endpoint(self, request)
                return answer
            finally:
                # Uploaded files wait in temporary files until the form is
                # closed, which Starlette leaves to the endpoint.
                await request.close()

        return handle

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Let the admin be mounted like any other ASGI app."""
        await self.app(scope, receive, send)
