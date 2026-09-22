from typing import TYPE_CHECKING, Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite.exceptions import AdminSiteError
from adminsite.http.saved import owner_of
from adminsite.http.templating import add_message
from adminsite.http.urls import Urls
from adminsite.i18n import gettext as _
from adminsite.imports import (
    ImportPlan,
    ImportProblem,
    build_plan,
    import_columns,
    load_plan,
    read_table,
    save_plan,
    template_csv,
)
from adminsite.security import Permission
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

MAX_BYTES = 20 * 1024 * 1024
# The preview shows this many rows, plus every row with a problem.
PREVIEW_ROWS = 50
PREVIEW_PROBLEMS = 200


async def show_form(
    admin: "Admin", request: Request, view: ModelView, error: str = ""
) -> Response:
    """The page for choosing a file."""
    await view.ensure(Permission.IMPORT, request=request)
    columns = [(path, view.label_for(path)) for path in import_columns(view, request)]
    return await admin.render(
        "import.html",
        request,
        {"view": view, "columns": columns, "error": error},
        status_code=422 if error else 200,
    )


def template_response(view: ModelView, request: Request) -> Response:
    """A CSV with just the header row, to fill in."""
    return Response(
        template_csv(view, request),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{view.name}.csv"'},
    )


async def preview(
    admin: "Admin", request: Request, view: ModelView, form: dict[str, Any]
) -> Response:
    """Check an uploaded file and show what importing it would do."""
    await view.ensure(Permission.IMPORT, request=request)
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return await show_form(admin, request, view, _("Choose a file to import."))
    if upload.size is not None and upload.size > MAX_BYTES:
        return await show_form(admin, request, view, _("Keep the file under 20 MB."))

    try:
        table = read_table(upload.filename, await upload.read())
        if len(table) - 1 > view.import_limit:
            raise ImportProblem(
                _(
                    "Import at most {count} rows at a time.",
                    count=f"{view.import_limit:,}",
                )
            )
        async with admin.database.session() as session:
            plan = await build_plan(view, session, table, request=request)
    except ImportProblem as problem:
        return await show_form(admin, request, view, str(problem))

    token = save_plan(view, owner_of(admin, request), table)
    return await admin.render(
        "import_preview.html",
        request,
        {
            "view": view,
            "plan": plan,
            "shown": rows_to_show(plan),
            "token": token,
            "filename": upload.filename,
        },
    )


def rows_to_show(plan: ImportPlan) -> list[Any]:
    """The first rows, and then every row that has a problem."""
    shown = plan.rows[:PREVIEW_ROWS]
    problems = [row for row in plan.rows[PREVIEW_ROWS:] if row.errors]
    return shown + problems[:PREVIEW_PROBLEMS]


async def run(admin: "Admin", request: Request, view: ModelView) -> Response:
    """Import the rows of a previewed file, each saved on its own."""
    await view.ensure(Permission.IMPORT, request=request)
    urls = Urls(request)
    table = load_plan(request.path_params["token"], view, owner_of(admin, request))
    if table is None:
        add_message(
            request, _("This import has expired. Choose the file again."), "error"
        )
        return RedirectResponse(f"{urls.list(view)}/import", status_code=303)

    created = changed = 0
    failed: list[str] = []
    async with admin.database.session() as session:
        plan = await build_plan(view, session, table, request=request)
        for row in plan.ready:
            record = None
            if row.key:
                record = await view.fetch_record(
                    session,
                    row.key,
                    paths=view.get_load_paths(request),
                    request=request,
                )
            try:
                await view.save(session, row.values, record=record, request=request)
            except AdminSiteError as error:
                failed.append(
                    _("row {number}: {reason}", number=row.number, reason=error)
                )
                continue
            if record is None:
                created += 1
            else:
                changed += 1

    skipped = plan.count("error")
    add_message(request, summary(view, created, changed, skipped, failed))
    return RedirectResponse(urls.list(view), status_code=303)


def summary(
    view: ModelView, created: int, changed: int, skipped: int, failed: list[str]
) -> str:
    """One line saying what the import did."""
    things = view.label_plural.lower()
    parts = [
        _(
            "Imported {created} new and changed {changed} {things}.",
            created=created,
            changed=changed,
            things=things,
        )
    ]
    if skipped:
        parts.append(_("Skipped {count} rows with problems.", count=skipped))
    if failed:
        shown = "; ".join(failed[:3])
        more = _(" and {count} more", count=len(failed) - 3) if len(failed) > 3 else ""
        parts.append(
            _(
                "Refused {count}: {rows}{more}.",
                count=len(failed),
                rows=shown,
                more=more,
            )
        )
    return " ".join(parts)
