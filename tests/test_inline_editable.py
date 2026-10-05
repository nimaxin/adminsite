import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, AdminSiteError, Field, Inline, Link, ModelView
from adminsite._http.forms import build_rows
from adminsite._http.listing import rows_context
from adminsite.database import Database
from adminsite.fields import BaseField, ComputedField, FileField, JSONField
from adminsite.files import LocalStorage
from adminsite.inspector import SQLAlchemyInspector
from adminsite.permissions import Permission, RequestAction
from tests.models import Customer, Order, OrderItem, OrderStatus, Product, Setting
from tests.support import Backend, count_queries, request_from


def line_count(order: Order) -> int:
    return len(order.items)


class Delivery(BaseModel):
    carriers: list[str] = []


def refusal(view: type[ModelView[Any]]) -> str:
    """The message a view is refused with, built as the admin builds it."""
    with pytest.raises(AdminSiteError) as raised:
        view(SQLAlchemyInspector())
    return str(raised.value)


def an_order(**values: Any) -> Order:
    """An order held in memory, as a page of the list holds one."""
    defaults: dict[str, Any] = {
        "id": 1,
        "status": OrderStatus.PENDING,
        "total": Decimal("10.00"),
        "note": None,
        "created_at": datetime(2026, 9, 1, 10, 30),
        "customer_id": 1,
        "items": [],
    }
    return Order(**{**defaults, **values})


class OrderView(ModelView[Order]):
    fields = [Order.id, Order.status, Order.total, Order.note]
    inline_editable_fields = [Order.status, Order.note]


class TestTheStartupChecks:
    def test_the_fields_named_are_kept_in_their_order(self) -> None:
        view = OrderView(SQLAlchemyInspector())

        assert view._settings.inline_editable_fields == ("status", "note")

    def test_a_field_the_view_does_not_have_is_refused(self) -> None:
        class Unknown(ModelView[Order]):
            fields = [Order.id, Order.status]
            inline_editable_fields = [Order.note]

        assert refusal(Unknown) == (
            'Unknown.inline_editable_fields names "note", which is not one of the '
            "view's fields. Add it to fields, or leave it out here."
        )

    def test_a_field_the_list_leaves_out_is_refused(self) -> None:
        class Unlisted(ModelView[Order]):
            fields = [Order.id, Order.status, Order.note]
            exclude_fields_from_list = [Order.note]
            inline_editable_fields = [Order.note]

        assert refusal(Unlisted) == (
            'Unlisted.inline_editable_fields names "note", which the list leaves '
            "out, so no cell holds it. Show it, or leave it out here."
        )

    def test_a_column_of_a_related_model_is_refused(self) -> None:
        class Related(ModelView[Order]):
            fields = [Order.id, Link(Order.customer, Customer.name)]
            inline_editable_fields = [Link(Order.customer, Customer.name)]

        assert refusal(Related) == (
            'Related.inline_editable_fields names "customer.name", a column of a '
            "related model, which no form changes. Leave it out here."
        )

    def test_a_computed_field_is_refused(self) -> None:
        class Computed(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count)]
            inline_editable_fields = ["lines"]

        assert refusal(Computed) == (
            'Computed.inline_editable_fields names "lines", which is worked out '
            "rather than stored, so nothing is saved. Leave it out here."
        )

    def test_a_key_the_database_fills_in_is_refused(self) -> None:
        class Keyed(ModelView[Order]):
            fields = [Order.status, Order.id]
            inline_editable_fields = [Order.id]

        assert refusal(Keyed) == (
            'Keyed.inline_editable_fields names "id", a key the database fills in, '
            "which nobody types. Leave it out here."
        )

    def test_a_field_the_edit_form_leaves_out_is_refused(self) -> None:
        class OffTheForm(ModelView[Order]):
            fields = [Order.id, Order.note]
            exclude_fields_from_edit = [Order.note]
            inline_editable_fields = [Order.note]

        assert refusal(OffTheForm) == (
            'OffTheForm.inline_editable_fields names "note", which the edit form '
            "leaves out. Put it on the edit form, or leave it out here."
        )

    def test_a_read_only_field_is_refused(self) -> None:
        class ReadOnly(ModelView[Order]):
            fields = [Order.id, Field(Order.note, read_only=True)]
            inline_editable_fields = [Order.note]

        assert refusal(ReadOnly) == (
            'ReadOnly.inline_editable_fields names "note", which cannot change '
            "once its record exists. Leave it out here."
        )

    def test_a_file_is_refused(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path / "uploads")

        class Files(ModelView[Product]):
            fields = [Product.name, FileField(Product.description, storage=storage)]
            inline_editable_fields = [Product.description]

        assert refusal(Files) == (
            'Files.inline_editable_fields names "description", a FileField, which '
            "needs the room of the edit form. Leave it out here."
        )

    def test_a_json_column_drawn_as_a_form_is_refused(self) -> None:
        class Documents(ModelView[Setting]):
            fields = [Setting.name, JSONField(Setting.options, schema=Delivery)]
            inline_editable_fields = [Setting.options]

        assert refusal(Documents) == (
            'Documents.inline_editable_fields names "options", a JSONField, which '
            "needs the room of the edit form. Leave it out here."
        )

    def test_the_first_column_is_refused(self) -> None:
        class First(ModelView[Order]):
            fields = [Order.note, Order.status]
            inline_editable_fields = [Order.note]

        assert refusal(First) == (
            'First.inline_editable_fields names "note", the list\'s first column, '
            "which opens the record. Put another column first, or leave it out "
            "here."
        )

    def test_a_column_only_offered_in_the_columns_menu_is_taken(self) -> None:
        class Offered(ModelView[Order]):
            fields = [Order.id, Order.status, Field(Order.note, hidden_in_list=True)]
            inline_editable_fields = [Order.note]

        assert Offered(SQLAlchemyInspector())._settings.inline_editable_fields == (
            "note",
        )


class LockedWhenShipped(OrderView):
    """Freezes the note of a shipped order, and hides the status from editing."""

    def get_readonly_fields(self, request: Request, record: Any = None) -> Any:
        if record is not None and record.status is OrderStatus.SHIPPED:
            return [Order.note]
        return []

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        return not (field.name == "status" and action is RequestAction.EDIT)


class TestWhichCellsChange:
    def test_the_named_columns_a_row_shows(self) -> None:
        view = OrderView(SQLAlchemyInspector())

        found = view._pages.editable_in_list(
            request_from(), an_order(), ["id", "note", "total", "status"]
        )

        assert found == ("note", "status")

    def test_not_a_column_the_list_does_not_show(self) -> None:
        view = OrderView(SQLAlchemyInspector())

        found = view._pages.editable_in_list(
            request_from(), an_order(), ["id", "total", "note"]
        )

        assert found == ("note",)

    def test_never_the_first_column_the_user_left_in_front(self) -> None:
        view = OrderView(SQLAlchemyInspector())

        found = view._pages.editable_in_list(
            request_from(), an_order(), ["status", "note"]
        )

        assert found == ("note",)

    def test_not_one_locked_for_the_record_or_kept_from_the_user(self) -> None:
        view = LockedWhenShipped(SQLAlchemyInspector())
        columns = ["id", "status", "note"]

        pending = view._pages.editable_in_list(request_from(), an_order(), columns)
        shipped = view._pages.editable_in_list(
            request_from(), an_order(status=OrderStatus.SHIPPED), columns
        )

        assert pending == ("note",)
        assert shipped == ()

    def test_none_without_the_setting(self) -> None:
        class Plain(ModelView[Order]):
            fields = [Order.id, Order.status]

        found = Plain(SQLAlchemyInspector())._pages.editable_in_list(
            request_from(), an_order(), ["id", "status"]
        )

        assert found == ()

    async def test_only_the_rows_the_user_may_change(self) -> None:
        class RefusesShipped(OrderView):
            async def allows(
                self, action: Permission | str, *, request: Request, record: Any = None
            ) -> bool:
                if action == Permission.EDIT and record is not None:
                    return record.status is not OrderStatus.SHIPPED
                return await super().allows(action, request=request, record=record)

        view = RefusesShipped(SQLAlchemyInspector())
        rows = [an_order(id=1), an_order(id=2, status=OrderStatus.SHIPPED)]

        drawn = await rows_context(view, request_from(), rows, ["id", "status"])

        assert drawn["editable"] == {"1": ("status",)}

    async def test_none_when_the_view_cannot_change_records(self) -> None:
        class Unchangeable(OrderView):
            can_edit = False

        view = Unchangeable(SQLAlchemyInspector())

        drawn = await rows_context(view, request_from(), [an_order()], ["id", "status"])

        assert drawn["editable"] == {}


class WithLines(OrderView):
    inlines = [Inline(Order.items, fields=[OrderItem.quantity])]


class TestReadingOneField:
    def test_only_that_field_is_read(self) -> None:
        view = WithLines(SQLAlchemyInspector())

        result = view._forms.parse(
            {"status": "paid", "note": "Left at the door", "items-count": "1"},
            record=an_order(),
            request=request_from(),
            paths=["status"],
        )

        assert result.values == {"status": OrderStatus.PAID}
        assert result.inline_rows == {}

    def test_a_value_it_cannot_read_is_reported(self) -> None:
        view = OrderView(SQLAlchemyInspector())

        result = view._forms.parse(
            {"status": "lost"},
            record=an_order(),
            request=request_from(),
            paths=["status"],
        )

        assert list(result.errors) == ["status"]

    async def test_only_that_field_is_drawn(self, database: Database) -> None:
        admin = Admin(database, views=[OrderView])
        view = admin.views.find("orders")
        assert view is not None

        async with database.session() as session:
            order = await session.get(Order, 1)
            rows = await build_rows(
                admin,
                view,
                session,
                record=order,
                request=request_from(),
                paths=["note"],
            )

        assert [row.path for row in rows] == ["note"]


class ChangesUnshipped(OrderView):
    """Lets nobody change a shipped order, from its form or from the list."""

    name = "unshipped"

    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        if action == Permission.EDIT and record is not None:
            return record.status is not OrderStatus.SHIPPED
        return await super().allows(action, request=request, record=record)


class PlainOrders(ModelView[Order]):
    name = "plain"
    fields = [Order.id, Order.status, Order.total, Order.note]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[OrderView, ChangesUnshipped, PlainOrders])
    async with serve(admin) as client:
        yield client


def editors(page: httpx.Response) -> list[str]:
    """The address each cell that changes in place fetches its editor from."""
    return re.findall(r'data-edit-url="([^"]+)"', page.text)


class TestTheList:
    async def test_each_named_cell_opens_its_editor(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        found = editors(listed)
        assert "/admin/orders/3/edit/status" in found
        assert "/admin/orders/3/edit/note" in found
        assert not any(url.endswith("/edit/total") for url in found)

    async def test_a_cell_is_a_button_that_names_what_it_changes(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        button = re.search(
            r'<button[^>]*data-edit-url="/admin/orders/3/edit/status"[^>]*>.*?</button>',
            listed.text,
            flags=re.DOTALL,
        )
        assert button is not None
        assert 'aria-haspopup="dialog"' in button.group(0)
        assert 'onclick="openValueEditor(this)"' in button.group(0)
        assert '<span class="sr-only">Edit Status</span>' in button.group(0)

    async def test_the_first_column_still_opens_the_record(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        assert not any(url.endswith("/edit/id") for url in editors(listed))
        assert 'href="/admin/orders/3"' in listed.text

    async def test_the_address_keeps_the_lists_query(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders?sort=-total")

        assert "/admin/orders/3/edit/status?sort=-total" in editors(listed)

    async def test_a_record_the_user_may_not_change_has_plain_cells(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/unshipped")

        found = editors(listed)
        assert "/admin/unshipped/3/edit/status" in found
        # Orders 1 and 4 are shipped.
        assert not any(url.startswith("/admin/unshipped/1/") for url in found)
        assert not any(url.startswith("/admin/unshipped/4/") for url in found)

    async def test_the_editor_is_on_the_page_only_where_cells_change(
        self, client: httpx.AsyncClient
    ) -> None:
        editing = await client.get("/admin/orders")
        plain = await client.get("/admin/plain")

        assert 'id="value-editor"' in editing.text
        assert 'id="value-editor"' not in plain.text
        assert editors(plain) == []

    async def test_every_page_has_a_place_for_messages(
        self, client: httpx.AsyncClient
    ) -> None:
        plain = await client.get("/admin/plain")

        assert re.search(
            r'<div class="[^"]*empty:hidden[^"]*" id="toasts"></div>', plain.text
        )

    async def test_it_costs_no_more_queries(self, backend: Backend) -> None:
        admin = Admin(backend.database, views=[OrderView, PlainOrders])
        async with serve(admin) as client:
            await client.get("/admin/orders")
            with count_queries(backend) as editing:
                await client.get("/admin/orders")
            with count_queries(backend) as plain:
                await client.get("/admin/plain")

        assert editing.count == plain.count
