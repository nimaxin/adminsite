import csv
import io
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from adminsite.query import QuerySpec
from adminsite.text import plain
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

# Rows are read and written in batches, so a large table never has to fit
# in memory at either end.
BATCH_SIZE = 500


def csv_rows(view: ModelView, records: Sequence[Any], paths: Sequence[str]) -> str:
    """Write records as CSV text, using what the list would show."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for record in records:
        writer.writerow([plain(view.display(record, path)) for path in paths])
    return buffer.getvalue()


def csv_header(view: ModelView, paths: Sequence[str]) -> str:
    """Write the heading row."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(
        [view.label_for(path) for path in paths]
    )
    return buffer.getvalue()


async def stream_csv(
    admin: "Admin",
    view: ModelView,
    spec: QuerySpec,
    request: Request,
    columns: Sequence[str] = (),
) -> AsyncIterator[str]:
    """Send the whole result a batch at a time.

    The columns are what the file holds; the spec says what to load, which
    is not the same, since a computed column loads whatever it reads.
    """
    paths = tuple(columns) or view.get_list_display(request)
    yield csv_header(view, paths)

    offset = 0
    async with admin.database.session() as session:
        while True:
            batch = await view.fetch_page(
                session,
                spec.replace(limit=BATCH_SIZE, offset=offset),
                request=request,
            )
            if not len(batch):
                return
            yield csv_rows(view, list(batch.rows), paths)
            if len(batch) < BATCH_SIZE:
                return
            offset += BATCH_SIZE
