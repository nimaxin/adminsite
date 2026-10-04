import csv
import io
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from adminsite._http.listing import read_list_request
from adminsite._http.requests import find_view
from adminsite.audit import AuditEntry, AuditEvent
from adminsite.audit._actor import actor_of
from adminsite.audit.store import record_or_warn
from adminsite.markup import plain
from adminsite.permissions import Permission
from adminsite.query import CountMode, QuerySpec
from adminsite.saved_views import clean_query
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin


if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "BATCH_SIZE",
    "FORMULA_STARTS",
    "as_cell",
    "csv_header",
    "csv_rows",
    "export_records",
    "list_query",
    "stream_csv",
]

# Rows are read and written in batches, so a large table never has to fit
# in memory at either end.
BATCH_SIZE = 500


def csv_rows(view: ModelView[Any], records: Sequence[Any], paths: Sequence[str]) -> str:
    """Write records as CSV text, using what the list would show."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for record in records:
        writer.writerow(
            [as_cell(plain(view._fields.display(record, path))) for path in paths]
        )
    return buffer.getvalue()


# What a spreadsheet takes as the start of a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def as_cell(text: str) -> str:
    """Text a spreadsheet reads as text, never as a formula.

    A value typed into the application by anyone, such as a customer's
    name, ends up in a file an administrator opens in Excel, which runs a
    cell starting with `=` as a formula. Such a cell is marked as text with
    a leading apostrophe, the way spreadsheets themselves do it. A number
    is left alone, so an amount below zero still adds up.
    """
    if not text or text[0] not in FORMULA_STARTS:
        return text
    try:
        Decimal(text.replace(",", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return "'" + text
    return text


def csv_header(view: ModelView[Any], paths: Sequence[str]) -> str:
    """Write the heading row."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(
        [view._fields.label_for(path) for path in paths]
    )
    return buffer.getvalue()


async def stream_csv(
    admin: "Admin",
    view: ModelView[Any],
    spec: QuerySpec,
    request: Request,
    columns: Sequence[str] = (),
) -> AsyncIterator[str]:
    """Send the whole result a batch at a time.

    The columns are what the file holds; the spec says what to load, which
    is not the same, since a computed column loads whatever it reads.
    """
    paths = view._pages.exported(
        tuple(columns) or view._pages.list_fields(request), request
    )
    yield csv_header(view, paths)

    offset = 0
    async with admin.database.session() as session:
        while True:
            batch = await view._reader.fetch_page(
                session,
                spec.replace(limit=BATCH_SIZE, offset=offset),
                request=request,
            )
            if not len(batch):
                return
            await view._reader.load_values(session, list(batch), paths, request=request)
            yield csv_rows(view, list(batch.rows), paths)
            if len(batch) < BATCH_SIZE:
                return
            offset += BATCH_SIZE


async def export_records(admin: "Admin", request: Request) -> Response:
    """Stream the current list as CSV, filters and all."""
    view = find_view(admin, request)
    await view._ensure(Permission.EXPORT, request=request)

    read = read_list_request(request, view)
    spec = view._reader.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
        paths=read.columns,
    ).replace(limit=None, offset=0, count=CountMode.NONE, keyset=False)

    if admin.audit is not None:
        # Written when the download starts: the list is the search and the
        # filters, since the rows of a large export are too many to name.
        await record_or_warn(
            admin.audit,
            [
                AuditEntry(
                    view=view.name,
                    record_key="",
                    event=AuditEvent.EXPORTED,
                    inputs=list_query(request.url.query),
                    **actor_of(request),
                )
            ],
        )

    filename = f"{view.name}.csv"
    return StreamingResponse(
        stream_csv(admin, view, spec, request, read.columns),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def list_query(query: str) -> dict[str, str | list[str]]:
    """The search, filters, sort and columns a list was asked for, by name."""
    found: dict[str, list[str]] = {}
    for key, value in parse_qsl(clean_query(query)):
        found.setdefault(key, []).append(value)
    return {
        key: values[0] if len(values) == 1 else values for key, values in found.items()
    }
