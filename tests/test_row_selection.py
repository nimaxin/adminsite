"""Rows are ticked by a click anywhere on them, once one is ticked."""

import httpx
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.database import Database
from tests.models import Order


class OrderView(ModelView[Order]):
    fields = ["id", "status", "total"]


class UndeletableOrders(OrderView):
    """A list with no action to run on its ticked rows."""

    can_delete = False


async def list_page(database: Database, view: type[ModelView[Order]]) -> str:
    app = Starlette()
    app.mount("/admin", Admin(database, views=[view]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        page = await client.get("/admin/orders")
    assert page.status_code == 200
    return page.text


class TestTickingRows:
    async def test_the_table_ticks_the_row_a_click_lands_on(
        self, database: Database
    ) -> None:
        page = await list_page(database, OrderView)
        table = page[page.index("<table") : page.index("<thead>")]

        assert '@click="pickRows($event)"' in table
        assert '@mousedown="keepRowText($event)"' in table
        assert "'[&_tbody_tr]:cursor-pointer'" in table
        assert "function pickRows(event)" in page

    async def test_the_whole_cell_of_the_box_ticks_it(self, database: Database) -> None:
        page = await list_page(database, OrderView)
        row = page.split("<tbody>", 1)[1].split("</tr>", 1)[0]
        cell = row[row.index("<td") : row.index('name="keys"')]

        assert "cursor-pointer" in cell

    async def test_a_list_with_no_rows_to_tick_leaves_clicks_alone(
        self, database: Database
    ) -> None:
        page = await list_page(database, UndeletableOrders)
        table = page[page.index("<table") : page.index("<thead>")]

        assert "pickRows" not in table
