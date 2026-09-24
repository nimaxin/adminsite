import datetime
import logging
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, select
from starlette.requests import Request

from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.http.urls import Urls
from adminsite.query import CountMode, Sort
from adminsite.security import Permission

if TYPE_CHECKING:
    from adminsite.admin import Admin

logger = logging.getLogger("adminsite")

# Where a widget's numbers come from: a select, or a function given the
# session that returns them.
Source = Select[Any] | Callable[[SessionAdapter], Awaitable[Any]]


class Widget:
    """One card on the overview page.

    Subclass it for a card of your own: set `template` to a template in
    your `template_dirs` and return what it needs from `load`. The template
    gets `widget` and `data`.
    """

    title: str = ""
    # How many of the four columns the card spans on a wide screen.
    width: int = 1
    template: str = ""

    async def allows(self, admin: "Admin", request: Request) -> bool:
        """Whether this user sees the card. Everyone does, unless you say."""
        return True

    async def load(self, admin: "Admin", request: Request) -> Any:
        """Read what the card shows."""
        return None


async def read_value(session: SessionAdapter, source: Source) -> Any:
    """One value from a select or a function."""
    if isinstance(source, Select):
        return await session.scalar(source)
    return await source(session)


async def read_rows(session: SessionAdapter, source: Source) -> list[Any]:
    """Rows from a select or a function."""
    if isinstance(source, Select):
        return list((await session.execute(source)).all())
    return list(await source(session))


@dataclass(frozen=True)
class StatData:
    """What a stat card shows."""

    value: str
    change: float | None = None


class Stat(Widget):
    """A single number, such as revenue this month, with an optional change.

    ```python
    Stat(
        "Revenue this month",
        select(func.sum(Order.total)).where(Order.created_at >= month_start),
        previous=select(func.sum(Order.total)).where(...last month...),
        format="€{:,.2f}",
    )
    ```
    """

    template = "adminsite/dashboard/stat.html"

    def __init__(
        self,
        title: str,
        value: Source,
        *,
        previous: Source | None = None,
        format: str = "{:,}",
        hint: str = "",
        link: str = "",
        width: int = 1,
    ) -> None:
        self.title = title
        self.value = value
        self.previous = previous
        self.format = format
        self.hint = hint
        self.link = link
        self.width = width

    async def load(self, admin: "Admin", request: Request) -> StatData:
        """Read the number, and the one it is compared with."""
        async with admin.database.session() as session:
            current = await read_value(session, self.value)
            before = (
                await read_value(session, self.previous)
                if self.previous is not None
                else None
            )
        change = None
        if current is not None and before:
            change = (float(current) - float(before)) / abs(float(before)) * 100
        shown = "—" if current is None else self.format.format(current)
        return StatData(shown, change)


@dataclass(frozen=True)
class Point:
    """One bar or point of a chart, already placed on a 100 high canvas."""

    label: str
    value: str
    x: float
    y: float
    height: float


@dataclass(frozen=True)
class Tick:
    """One line across a chart, with the value it stands for."""

    label: str
    # How far up the chart it sits, from 0 at the bottom to 100 at the top.
    position: float


@dataclass(frozen=True)
class ChartData:
    """A chart ready to draw."""

    points: Sequence[Point]
    width: float
    highest: str
    kind: str
    ticks: Sequence[Tick] = ()

    @property
    def line(self) -> str:
        """The points of a line chart, as SVG wants them."""
        return " ".join(f"{point.x + 5:.2f},{point.y:.2f}" for point in self.points)

    @property
    def axis(self) -> list[str]:
        """A few labels under the chart: the first, the middle and the last."""
        labels = [point.label for point in self.points]
        if len(labels) <= 3:
            return labels
        return [labels[0], labels[len(labels) // 2], labels[-1]]


class Chart(Widget):
    """Bars or a line over labelled values, drawn as plain SVG.

    The source gives rows of a label and a value, such as orders per day:

    ```python
    day = func.date(Order.created_at)
    Chart(
        "Orders per day",
        select(day, func.count()).group_by(day).order_by(day),
        kind="line",
    )
    ```
    """

    template = "adminsite/dashboard/chart.html"

    def __init__(
        self,
        title: str,
        rows: Source,
        *,
        kind: str = "bar",
        format: str = "{:,}",
        width: int = 2,
    ) -> None:
        if kind not in ("bar", "line"):
            raise ValueError(f"A chart is 'bar' or 'line', not {kind!r}.")
        self.title = title
        self.rows = rows
        self.kind = kind
        self.format = format
        self.width = width

    async def load(self, admin: "Admin", request: Request) -> ChartData:
        """Read the rows and place them on the canvas."""
        async with admin.database.session() as session:
            rows = await read_rows(session, self.rows)
        values = [float(value or 0) for _, value in rows]
        top, step = round_axis(max(values, default=0))
        points = [
            Point(
                label=label_text(label),
                value=self.format.format(raw if raw is not None else 0),
                x=index * 10,
                y=100 - value / top * 100,
                height=value / top * 100,
            )
            for index, ((label, raw), value) in enumerate(
                zip(rows, values, strict=True)
            )
        ]
        highest = self.format.format(max((raw or 0 for _, raw in rows), default=0))
        ticks = [
            Tick(self.tick_label(step * number), step * number / top * 100)
            for number in range(round(top / step) + 1)
        ]
        return ChartData(points, max(len(points), 1) * 10, highest, self.kind, ticks)

    def tick_label(self, value: float) -> str:
        """A value on the axis, without cents where it is a whole number."""
        text = self.format.format(value if value % 1 else int(value))
        return text[:-3] if value % 1 == 0 and text.endswith(".00") else text


# A round top for an axis and the gap between its lines, per power of ten:
# up to 1 in steps of 0.2, up to 2 in steps of 0.5, and so on.
ROUND_TOPS = ((1, 0.2), (2, 0.5), (2.5, 0.5), (5, 1), (10, 2))


def round_axis(highest: float) -> tuple[float, float]:
    """The round number an axis goes up to, and the step between its lines.

    A highest value of 181 gives an axis to 200 in steps of 50, so the lines
    across the chart fall on numbers people read at a glance.
    """
    if highest <= 0:
        return 1.0, 0.2
    power = 10 ** math.floor(math.log10(highest))
    for multiple, step in ROUND_TOPS:
        if highest <= multiple * power:
            return multiple * power, step * power
    return 10 * power, 2 * power


def label_text(label: Any) -> str:
    """Write a chart label short: dates as "Sep 14", the rest as text."""
    if isinstance(label, datetime.datetime):
        return label.strftime("%b %d %H:%M")
    if isinstance(label, datetime.date):
        return label.strftime("%b %d")
    if isinstance(label, Decimal | float):
        return f"{label:,}"
    if isinstance(label, str) and len(label) == 10:
        # SQLite answers date() with text, so a day reads the same there too.
        try:
            return label_text(datetime.date.fromisoformat(label))
        except ValueError:
            pass
    return str(label)


@dataclass(frozen=True)
class RecentItem:
    """One record in a recent records card."""

    title: str
    url: str
    detail: str = ""


class RecentRecords(Widget):
    """The latest records of a view, linking to each.

    ```python
    RecentRecords("Latest orders", "orders", sort="-created_at", detail="total")
    ```

    It reads through the view, so the view's permissions and scope apply.
    """

    template = "adminsite/dashboard/recent.html"

    def __init__(
        self,
        title: str,
        view: str,
        *,
        sort: str = "",
        detail: str = "",
        limit: int = 5,
        width: int = 2,
    ) -> None:
        self.title = title
        self.view = view
        self.sort = sort
        self.detail = detail
        self.limit = limit
        self.width = width

    async def allows(self, admin: "Admin", request: Request) -> bool:
        """Shown to whoever may open the view."""
        found = admin.views.find(self.view)
        return found is not None and await found.allows(
            Permission.VIEW, request=request
        )

    async def load(self, admin: "Admin", request: Request) -> list[RecentItem]:
        """Read the latest records through the view."""
        view = admin.views.find(self.view)
        if view is None:
            return []
        sort = (Sort.parse(self.sort),) if self.sort else ()
        spec = view.build_spec(request=request, sort=sort).replace(
            limit=self.limit, offset=0, count=CountMode.NONE, keyset=False
        )
        urls = Urls(request)
        opens_detail = await view.allows(Permission.DETAIL, request=request)
        async with admin.database.session() as session:
            page = await view.fetch_page(session, spec, request=request)
            return [
                RecentItem(
                    view.title_of(record),
                    urls.detail(view, view.identity_of(record))
                    if opens_detail
                    else urls.edit(view, view.identity_of(record)),
                    view.display(record, self.detail) if self.detail else "",
                )
                for record in page
            ]


@dataclass(frozen=True)
class CountItem:
    """One view and how many records it has."""

    label: str
    url: str
    count: int
    group: str = ""


class ModelCounts(Widget):
    """How many records each view holds: the overview when nothing else is set."""

    template = "adminsite/dashboard/counts.html"
    width = 4

    async def load(self, admin: "Admin", request: Request) -> list[CountItem]:
        """Count the records of every view this user may open, within its scope."""
        urls = Urls(request)
        found = []
        async with admin.database.session() as session:
            for view in await admin.views_allowing(request):
                statement = view.repository.base_statement(view.scope_for(request))
                total = await session.scalar(
                    select(func.count()).select_from(statement.subquery())
                )
                found.append(
                    CountItem(
                        view.label_plural, urls.list(view), int(total or 0), view.group
                    )
                )
        return found


@dataclass(frozen=True)
class LoadedWidget:
    """A card with what it read, or the reason it could not read it."""

    widget: Widget
    data: Any = None
    failed: bool = False


async def load_dashboard(
    admin: "Admin", request: Request, widgets: Sequence[Widget]
) -> list[LoadedWidget]:
    """Read every card this user may see.

    A card that fails is shown as failed and logged, so one broken query
    does not take the whole overview down with it.
    """
    loaded = []
    for widget in widgets:
        if not await widget.allows(admin, request):
            continue
        try:
            loaded.append(LoadedWidget(widget, await widget.load(admin, request)))
        except Exception:
            logger.exception("The %r card on the overview failed.", widget.title)
            loaded.append(LoadedWidget(widget, failed=True))
    return loaded
