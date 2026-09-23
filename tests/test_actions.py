import re

import httpx
import pytest
from sqlalchemy import func, select
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.actions import Selection, action
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError, RefusedError
from adminsite.fields import ChoiceField, StringField
from adminsite.query import QuerySpec
from adminsite.security import Permission
from tests.models import Order, OrderStatus, Product


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    list_filter = ("status",)
    search_fields = ("customer.name",)
    page_size = 3

    @action("Mark as shipped", confirm="Mark these as shipped?")
    async def ship(self, selection: Selection) -> str:
        changed = await selection.update(status=OrderStatus.SHIPPED)
        return f"{changed} orders marked as shipped."

    @action("Discard", dangerous=True, permission=Permission.DELETE)
    async def discard(self, selection: Selection) -> str:
        removed = await selection.delete()
        return f"{removed} orders deleted."

    @action("Never")
    async def never(self, selection: Selection) -> str:
        raise RefusedError("Not while the shop is open.")

    @action("Count one by one")
    async def one_by_one(self, selection: Selection) -> str:
        records = await selection.records()
        return f"{len(records)} orders seen."

    @action(
        "Add a note",
        confirm="Add this note to the chosen orders?",
        inputs=[
            StringField("text", label="Note", required=True, max_length=200),
            ChoiceField(
                "urgency",
                choices=(("low", "Low"), ("high", "High")),
                required=True,
            ),
        ],
    )
    async def add_note(self, selection: Selection, text: str, urgency: str) -> str:
        changed = await selection.update(note=f"[{urgency}] {text}")
        return f"Note added to {changed} orders."


class ProductView(ModelView, model=Product):
    list_display = ("id", "name", "price")


@pytest.fixture
def admin(database: Database) -> Admin:
    site = Admin(database, title="Shop")
    site.add_view(OrderView)
    site.add_view(ProductView)
    return site


@pytest.fixture
def client(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def status_count(database: Database, status: OrderStatus) -> int:
    async with database.session() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(Order).where(Order.status == status)
            )
            or 0
        )


class TestDeclaringActions:
    def test_actions_are_found_on_the_view(self) -> None:
        names = [item.name for item in OrderView().get_actions()]

        assert set(names) == {"ship", "discard", "never", "one_by_one", "add_note"}

    def test_an_action_with_inputs_opens_a_dialog(self) -> None:
        found = OrderView().action_named("add_note")

        assert found.needs_dialog is True
        assert [item.name for item in found.inputs] == ["text", "urgency"]

    def test_an_input_cannot_take_a_name_the_form_uses(self) -> None:
        with pytest.raises(AdminSiteError, match="cannot be called 'keys'"):
            action("Broken", inputs=[StringField("keys")])

    def test_an_action_carries_its_label_and_confirmation(self) -> None:
        ship = OrderView().action_named("ship")

        assert ship.label == "Mark as shipped"
        assert ship.needs_confirming is True
        assert ship.dangerous is False

    def test_a_label_is_made_from_the_name_when_none_is_given(self) -> None:
        assert OrderView().action_named("one_by_one").label == "Count one by one"


class TestRunningActions:
    async def test_it_changes_only_the_chosen_rows(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        before = await status_count(database, OrderStatus.SHIPPED)

        response = await client.post(
            "/admin/orders/action/ship", data={"keys": ["3", "7"]}
        )

        assert response.status_code == 303
        assert await status_count(database, OrderStatus.SHIPPED) == before + 2

    async def test_it_can_cover_every_row_matching_the_filter(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post(
            "/admin/orders/action/ship?status=PENDING",
            data={"everything": "1"},
        )

        assert response.status_code == 303
        assert await status_count(database, OrderStatus.PENDING) == 0
        assert await status_count(database, OrderStatus.SHIPPED) == 4

    async def test_selecting_everything_respects_the_search(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post("/admin/orders/action/ship?q=lena", data={"everything": "1"})

        assert await status_count(database, OrderStatus.SHIPPED) == 3

    async def test_a_dangerous_action_deletes(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post("/admin/orders/action/discard", data={"keys": "7"})

        assert response.status_code == 303
        async with database.session() as session:
            assert await session.get(Order, 7) is None

    async def test_an_action_can_work_record_by_record(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/admin/orders/action/one_by_one", data={"keys": ["1", "2"]}
        )

        assert response.status_code == 303

    async def test_a_refused_action_changes_nothing(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        before = await status_count(database, OrderStatus.PENDING)

        await client.post("/admin/orders/action/never", data={"everything": "1"})

        assert await status_count(database, OrderStatus.PENDING) == before

    async def test_an_unknown_action_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post("/admin/orders/action/fly", data={"keys": "1"})

        assert answer.status_code == 404


class TestActionInputs:
    async def test_the_values_reach_the_action(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post(
            "/admin/orders/action/add_note",
            data={"keys": ["1", "2"], "text": "Call first", "urgency": "high"},
        )

        assert response.status_code == 303
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.note == "[high] Call first"

    async def test_a_missing_value_stops_the_action(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post(
            "/admin/orders/action/add_note",
            data={"keys": ["1"], "text": "", "urgency": "high"},
        )

        assert response.status_code == 303
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.note is None

    async def test_a_value_outside_the_choices_stops_the_action(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post(
            "/admin/orders/action/add_note",
            data={"keys": ["1"], "text": "Hello", "urgency": "extreme"},
        )

        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.note is None

    async def test_the_reason_is_shown_on_the_list(self, database: Database) -> None:
        site = Admin(database, title="Shop", secret_key="for-the-messages")
        site.add_view(OrderView)
        app = Starlette()
        app.mount("/admin", site)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as session_client:
            page = await session_client.get("/admin/orders")
            token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
            assert token is not None

            response = await session_client.post(
                "/admin/orders/action/add_note",
                data={
                    "keys": ["1"],
                    "text": "",
                    "urgency": "high",
                    "_csrf": token.group(1),
                },
                follow_redirects=True,
            )

            assert "Add a note was not done." in response.text
            assert "Note: This field is required." in response.text


class TestDialogs:
    async def test_the_list_carries_a_dialog_for_each_action_that_asks(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders")

        assert 'id="action-add_note"' in response.text
        assert 'id="action-ship"' in response.text
        assert 'id="action-one_by_one"' not in response.text

    async def test_the_dialog_holds_the_inputs(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders")
        dialog = response.text.split('id="action-add_note"')[1].split("</dialog>")[0]

        assert 'name="text"' in dialog
        assert 'name="urgency"' in dialog
        assert "Add this note to the chosen orders?" in dialog

    async def test_the_browser_confirm_is_gone(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders")

        assert "confirm(" not in response.text


class TestSelection:
    async def test_a_selection_counts_what_it_covers(self, database: Database) -> None:
        view = OrderView()
        async with database.session() as session:
            everything = Selection(
                view=view,
                session=session,
                spec=QuerySpec(),
                everything=True,
            )
            picked = Selection(
                view=view, session=session, spec=QuerySpec(), keys=("1", "2")
            )

            assert await everything.count() == 7
            assert await picked.count() == 2

    async def test_a_selection_of_everything_follows_the_filters(
        self, database: Database
    ) -> None:
        view = OrderView()
        spec = view.build_spec(search="lena")
        async with database.session() as session:
            selection = Selection(
                view=view, session=session, spec=spec, everything=True
            )

            assert await selection.count() == 2

    async def test_updating_touches_only_the_selection(
        self, database: Database
    ) -> None:
        view = OrderView()
        async with database.session() as session:
            selection = Selection(
                view=view, session=session, spec=QuerySpec(), keys=("1",)
            )

            changed = await selection.update(note="looked at")
            await session.commit()

            assert changed == 1
            noted = await session.scalar(
                select(func.count()).select_from(Order).where(Order.note.is_not(None))
            )
            assert noted == 1


class TestExport:
    async def test_it_sends_a_csv_file(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "orders.csv" in response.headers["content-disposition"]

    async def test_it_has_a_heading_row_and_every_record(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/export")
        lines = [line for line in response.text.splitlines() if line]

        assert lines[0] == "Id,Status,Total"
        assert len(lines) == 8

    async def test_it_exports_the_filtered_list_not_the_page(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/export?status=SHIPPED")
        lines = [line for line in response.text.splitlines() if line]

        assert len(lines) == 3

    async def test_values_are_written_the_way_they_are_shown(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/export?status=SHIPPED")

        assert "Shipped" in response.text
