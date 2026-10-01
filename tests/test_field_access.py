"""can_access_field keeps a field from some people on the pages it names."""

import csv
import io
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import (
    Admin,
    AdminSiteError,
    BaseField,
    Descending,
    Field,
    Inline,
    Link,
    ModelView,
    RequestAction,
    Sort,
)
from adminsite.audit import AuditLog
from adminsite.backends.sqlalchemy import Database, TextFilter
from adminsite.imports import build_plan, import_columns, read_table
from tests.models import Customer, Order, OrderItem, OrderStatus, Shelf


def role_of(request: Any) -> str:
    """Who is asking: a header on a request, or the string a test passes."""
    if isinstance(request, Request):
        return request.headers.get("x-role", "")
    return str(request or "")


class Orders(ModelView[Order]):
    fields = [
        Order.id,
        Order.customer,
        Link(Order.customer, Customer.email),
        Order.status,
        Order.total,
        Order.note,
    ]

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return field.name != "total" or role_of(request) == "manager"


class NotedOrders(ModelView[Order]):
    """Staff write a note on a new order, and only managers read it after."""

    name = "noted_orders"
    fields = [Order.id, Order.customer, Order.status, Order.note]

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return (
            field.name != "note"
            or role_of(request) == "manager"
            or action is RequestAction.CREATE
        )


class TestEachPage:
    def test_a_field_is_kept_from_some_people(self) -> None:
        view = Orders()

        for page in (
            view._pages.list_fields,
            view._pages.detail_fields,
            view._pages.form_fields,
        ):
            assert "total" not in page("staff")
            assert "total" in page("manager")

    def test_the_edit_form_and_the_export_ask_too(self) -> None:
        view = Orders()

        assert "total" not in view._pages.form_fields("staff", Order(id=1))
        assert view._pages.exported(["id", "total"], "staff") == ("id",)
        assert view._pages.exported(["id", "total"], "manager") == ("id", "total")

    def test_each_page_is_named_to_it(self) -> None:
        asked: list[tuple[str, RequestAction]] = []

        class Asking(ModelView[Order]):
            fields = [Order.id, Order.status]

            def can_access_field(
                self, request: Any, field: BaseField, action: RequestAction
            ) -> bool:
                asked.append((field.name, action))
                return True

        view = Asking()
        view._pages.list_fields()
        view._pages.detail_fields()
        view._pages.form_fields()
        view._pages.form_fields(record=Order(id=1))
        view._pages.exported(["id"])

        assert {action for _name, action in asked} == set(RequestAction)

    def test_a_column_of_a_related_model_is_named_by_its_path(self) -> None:
        names: set[str] = set()

        class Asking(ModelView[Order]):
            fields = [Order.id, Link(Order.customer, Customer.email), "customer.name"]

            def can_access_field(
                self, request: Any, field: BaseField, action: RequestAction
            ) -> bool:
                names.add(field.name)
                return True

        Asking()._pages.list_fields()

        assert names == {"id", "customer.email", "customer.name"}

    def test_a_hidden_column_is_not_offered_either(self) -> None:
        class Offered(Orders):
            fields = [Order.id, Field(Order.total, hidden_in_list=True)]

        view = Offered()

        assert view._pages.column_choices("staff") == ("id",)
        assert view._pages.column_choices("manager") == ("id", "total")

    def test_the_list_cannot_be_sorted_by_it(self) -> None:
        view = Orders()

        assert "total" not in view._pages.readable_paths("staff")
        assert "total" in view._pages.readable_paths("manager")

    def test_the_create_form_alone_does_not_make_it_readable(self) -> None:
        view = NotedOrders()

        assert "note" in view._pages.form_fields("staff")
        assert "note" not in view._pages.readable_paths("staff")
        assert "note" in view._pages.readable_paths("manager")

    def test_the_form_does_not_read_it_back(self) -> None:
        submitted = {"customer": "1", "status": "paid", "total": "0", "note": ""}

        result = Orders()._forms.parse(submitted, record=Order(id=1), request="staff")

        assert "total" not in result.values
        assert result.values["status"] == OrderStatus.PAID


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[Orders], api=True)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def headed_total(page: str) -> bool:
    """Whether a page shows a Total heading, in the list or on the record."""
    return re.search(r">\s*Total\s*<", page) is not None


class TestWhereItShows:
    @pytest.mark.parametrize(("role", "shown"), [("staff", False), ("manager", True)])
    async def test_the_pages_the_export_and_the_api_follow_it(
        self, client: httpx.AsyncClient, role: str, shown: bool
    ) -> None:
        headers = {"x-role": role}

        listed = await client.get("/admin/orders", headers=headers)
        record = await client.get("/admin/orders/1", headers=headers)
        form = await client.get("/admin/orders/1/edit", headers=headers)
        exported = await client.get("/admin/orders/export", headers=headers)
        api = await client.get("/admin/-/api/orders/1", headers=headers)

        assert headed_total(listed.text) is shown
        assert headed_total(record.text) is shown
        assert ('name="total"' in form.text) is shown
        assert ("Total" in next(csv.reader(io.StringIO(exported.text)))) is shown
        assert ("total" in api.json()) is shown

    async def test_the_api_does_not_write_it(self, client: httpx.AsyncClient) -> None:
        answer = await client.patch(
            "/admin/-/api/orders/1", json={"total": "0"}, headers={"x-role": "staff"}
        )

        assert answer.status_code == 422, answer.text
        assert "total" in answer.json()["errors"]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestTheCreateFormAlone:
    async def test_the_api_and_the_sort_leave_the_field_out(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            for key, note in ((1, "b"), (2, "a")):
                order = await session.get(Order, key)
                assert order is not None
                order.note = note
            await session.commit()
        admin = Admin(database, views=[NotedOrders, ModelView[Customer]], api=True)

        async def sorted_keys(api: httpx.AsyncClient, role: str) -> list[list[str]]:
            pages = [
                await api.get(
                    f"/admin/-/api/noted_orders?sort={sort}", headers={"x-role": role}
                )
                for sort in ("note", "-note")
            ]
            return [[item["key"] for item in page.json()["items"]] for page in pages]

        async with serve(admin) as api:
            staff = await api.get(
                "/admin/-/api/noted_orders/2", headers={"x-role": "staff"}
            )
            staff_sorts = await sorted_keys(api, "staff")
            manager_sorts = await sorted_keys(api, "manager")

        assert "note" not in staff.json()
        assert staff_sorts[0] == staff_sorts[1]
        assert manager_sorts[0] != manager_sorts[1]


class CustomerView(ModelView[Customer]):
    pass


class EditOnlyLink(ModelView[Order]):
    """Changes the customer on the edit form alone, and names it nowhere else."""

    name = "corrected_orders"
    fields = [
        Order.id,
        Order.status,
        Field(Order.customer, exclude_from_create=True, exclude_from_detail=True),
    ]


class SupportOrders(ModelView[Order]):
    """Lets support change the customer, which no other page shows."""

    name = "support_orders"
    fields = [Order.id, Order.status, Order.customer]

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return field.name != "customer" or action is RequestAction.EDIT


class TestALinkOnlyTheEditFormHas:
    @pytest.mark.parametrize("name", ["corrected_orders", "support_orders"])
    async def test_the_record_is_loaded_with_it(
        self, database: Database, name: str
    ) -> None:
        admin = Admin(database, views=[EditOnlyLink, SupportOrders, CustomerView])
        async with serve(admin) as client:
            record = await client.get(f"/admin/{name}/1")
            form = await client.get(f"/admin/{name}/1/edit")

        assert record.status_code == 200
        assert form.status_code == 200
        assert re.search(r'<option value="1"\s+selected>\s*Lena Fischer', form.text)


class ImportedCustomers(ModelView[Customer]):
    """Keeps email from everyone, and region off the form for a new record."""

    name = "imported_customers"
    fields = [Customer.name, Customer.email, Customer.region]
    can_import = True

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        if field.name == "email":
            return False
        return field.name != "region" or action is not RequestAction.CREATE


class StatusOnce(ModelView[Order]):
    """Staff pick the status of a new order, and only managers read it after."""

    name = "status_once"
    fields = [Order.id, Order.customer, Order.status, Order.note]
    can_import = True

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return (
            field.name != "status"
            or role_of(request) == "manager"
            or action is RequestAction.CREATE
        )


class TestAnImport:
    async def test_it_takes_the_fields_one_of_the_forms_writes(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[ImportedCustomers])
        async with serve(admin) as client:
            template = await client.get("/admin/imported_customers/import/template")

        assert import_columns(ImportedCustomers(), "staff") == ("id", "name", "region")
        assert template.text.strip() == "id,name,region"

    async def test_a_field_nobody_shows_them_is_never_compared(
        self, database: Database
    ) -> None:
        guesses = "id,status\n1,Pending\n1,Paid\n1,Shipped\n1,Refunded\n1,\n"
        async with database.session() as session:
            plan = await build_plan(
                StatusOnce(),
                session,
                read_table("a.csv", guesses.encode()),
                request="staff",
            )

        cannot = {"status": "This field cannot be changed."}
        assert [row.errors for row in plan.rows] == [cannot] * 4 + [{}]


class Narrowed(ModelView[Order]):
    """Searched, filtered and sorted by fields staff may not see."""

    name = "narrowed"
    fields = [Order.id, Order.status, Order.total, Order.note]
    searchable_fields = [Order.note]
    list_filters = [Order.status, Order.total]
    fields_default_sort = [Descending(Order.total)]

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return field.name not in ("total", "note") or role_of(request) == "manager"


class Items(ModelView[OrderItem]):
    name = "items"
    fields = [OrderItem.order, OrderItem.quantity]


class TestSearchFiltersAndSort:
    def test_a_refused_field_is_not_searched(self) -> None:
        view = Narrowed()

        assert view._pages.search_paths("staff") == ()
        assert view._pages.search_paths("manager") == ("note",)

    def test_nor_offered_as_a_filter(self) -> None:
        view = Narrowed()

        assert [item.name for item in view._pages.list_filters("staff")] == ["status"]
        assert [item.name for item in view._pages.list_filters("manager")] == [
            "status",
            "total",
        ]

    def test_nor_sorted_by_at_first(self) -> None:
        view = Narrowed()

        assert view._pages.default_sort("staff") == ()
        assert view._pages.default_sort("manager") == (Sort("total", descending=True),)

    def test_a_filter_that_names_no_field_is_kept(self) -> None:
        class Custom(Narrowed):
            list_filters = [TextFilter("anything", label="Anything")]

        assert [item.name for item in Custom()._pages.list_filters("staff")] == [
            "anything"
        ]


@pytest.fixture
async def narrowed(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with database.session() as session:
        order = await session.get(Order, 3)
        assert order is not None
        order.note = "vip-discount-XYZ"
        await session.commit()
    admin = Admin(database, views=[Narrowed, Items], api=True)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def keys_listed(page: httpx.Response) -> set[str]:
    """The orders a list or the palette links to, or a picker offers."""
    found = re.findall(r'href="/admin/narrowed/(\d+)"|data-value="(\d+)"', page.text)
    return {linked or offered for linked, offered in found}


class TestTheyCannotReadItBack:
    async def test_the_filter_is_not_offered_and_is_ignored(
        self, narrowed: httpx.AsyncClient
    ) -> None:
        staff = {"x-role": "staff"}

        page = await narrowed.get("/admin/narrowed", headers=staff)
        everything = await narrowed.get("/admin/-/api/narrowed", headers=staff)
        filtered = await narrowed.get("/admin/-/api/narrowed?total=100,", headers=staff)
        managed = await narrowed.get(
            "/admin/-/api/narrowed?total=100,", headers={"x-role": "manager"}
        )

        assert 'id="filter-status"' in page.text
        assert 'id="filter-total"' not in page.text
        assert filtered.json()["total"] == everything.json()["total"] == 7
        assert managed.json()["total"] == 3

    @pytest.mark.parametrize(
        "url",
        [
            "/admin/narrowed?q={term}",
            "/admin/-/search?q={term}",
            "/admin/items/lookup/order?q={term}",
        ],
    )
    async def test_a_search_does_not_find_what_it_holds(
        self, narrowed: httpx.AsyncClient, url: str
    ) -> None:
        async def found(term: str, role: str) -> set[str]:
            page = await narrowed.get(url.format(term=term), headers={"x-role": role})
            assert page.status_code == 200, page.text
            return keys_listed(page)

        # A search staff make finds the same records, whatever the note holds.
        assert await found("vip-discount", "staff") == await found(
            "nothing-like-this", "staff"
        )
        assert await found("vip-discount", "manager") == {"3"}

    async def test_nor_does_the_api(self, narrowed: httpx.AsyncClient) -> None:
        staff = await narrowed.get(
            "/admin/-/api/narrowed?q=vip-discount", headers={"x-role": "staff"}
        )
        manager = await narrowed.get(
            "/admin/-/api/narrowed?q=vip-discount", headers={"x-role": "manager"}
        )

        assert staff.json()["total"] == 7
        assert [item["key"] for item in manager.json()["items"]] == ["3"]


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def audited(
    database: Database, log: AuditLog
) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[Orders], api=True, audit=log)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestTheHistory:
    @pytest.mark.parametrize(("role", "shown"), [("staff", False), ("manager", True)])
    async def test_it_leaves_out_what_a_refused_field_held(
        self, audited: httpx.AsyncClient, role: str, shown: bool
    ) -> None:
        manager = {"x-role": "manager"}
        changed = await audited.patch(
            "/admin/-/api/orders/1",
            json={"total": "12345.67", "note": "Gift wrap"},
            headers=manager,
        )
        await audited.get("/admin/orders/export?total=4321,", headers=manager)
        headers = {"x-role": role}

        record = await audited.get("/admin/orders/1", headers=headers)
        activity = await audited.get("/admin/-/activity", headers=headers)

        assert changed.status_code == 200, changed.text
        assert ("12,345.67" in record.text) is shown
        assert ("12,345.67" in activity.text) is shown
        assert ("4321," in activity.text) is shown
        # The fields this user may see still show what they held.
        assert "Gift wrap" in activity.text

    @pytest.mark.parametrize(("role", "shown"), [("staff", False), ("manager", True)])
    async def test_it_leaves_out_a_filter_named_apart_from_its_field(
        self, database: Database, log: AuditLog, role: str, shown: bool
    ) -> None:
        class Filtered(ModelView[Order]):
            name = "orders"
            fields = [Order.id, Order.status, Order.note]
            list_filters = [
                Order.status,
                Link(Order.customer, Customer.email),
                TextFilter("note_has", path="note"),
            ]

            def can_access_field(
                self, request: Any, field: BaseField, action: RequestAction
            ) -> bool:
                refused = field.name in ("note", "customer.email")
                return not refused or role_of(request) == "manager"

        app = Starlette()
        app.mount("/admin", Admin(database, views=[Filtered], audit=log))
        manager = {"x-role": "manager"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            for query in (
                "customer__email=secret-mail",
                "note_has=secret-note",
                "status=PAID",
            ):
                exported = await client.get(
                    f"/admin/orders/export?{query}", headers=manager
                )
                assert exported.status_code == 200
            activity = await client.get("/admin/-/activity", headers={"x-role": role})

        assert ("secret-mail" in activity.text) is shown
        assert ("secret-note" in activity.text) is shown
        assert "PAID" in activity.text


class TestGetReadonlyFields:
    def test_a_field_is_locked_for_one_record(self) -> None:
        class Shipped(ModelView[Order]):
            fields = [Order.customer, Order.status, Order.note]

            def get_readonly_fields(
                self, request: Any, record: Order | None
            ) -> list[Any]:
                if record is not None and record.status is OrderStatus.SHIPPED:
                    return [Order.customer, Order.status]
                return []

        view = Shipped()
        shipped = Order(id=1, status=OrderStatus.SHIPPED)
        pending = Order(id=2, status=OrderStatus.PENDING)

        assert view._pages.readonly_paths(None, shipped) == ("customer", "status")
        assert view._pages.readonly_paths(None, pending) == ()
        result = view._forms.parse(
            {"customer": "2", "status": "paid", "note": "Gift"}, record=shipped
        )
        assert result.values == {"note": "Gift"}

    def test_a_field_marked_read_only_needs_no_answer(self) -> None:
        class Fixed(ModelView[Order]):
            fields = [Order.status, Field(Order.total, read_only=True)]

        assert Fixed()._pages.readonly_paths(None, Order(id=1)) == ("total",)

    def test_a_key_marked_read_only_is_locked_on_both_forms(self) -> None:
        class Locked(ModelView[Shelf]):
            fields = [Field(Shelf.aisle, read_only=True), Shelf.slot, Shelf.label]

        view = Locked()
        shelf = Shelf(aisle="A", slot=1)
        result = view._forms.parse(
            {"aisle": "Z", "slot": "2", "label": "Linen"}, record=shelf
        )

        assert view._pages.readonly_paths() == ("aisle",)
        assert view._pages.readonly_paths(None, shelf) == ("aisle", "slot")
        assert result.values == {"label": "Linen"}

    def test_a_misspelt_name_is_refused(self) -> None:
        class Misspelt(ModelView[Order]):
            def get_readonly_fields(
                self, request: Any, record: Order | None
            ) -> list[str]:
                return ["stauts"]

        with pytest.raises(AdminSiteError) as raised:
            Misspelt()._pages.readonly_paths()

        assert str(raised.value).startswith(
            "Misspelt.get_readonly_fields: Order has no column or relationship "
            '"stauts".'
        )


class OrderLines(ModelView[Order]):
    """Orders whose lines show their price to managers alone."""

    name = "orders"
    fields = [Order.customer, Order.status, Order.note, Order.created_at]
    inlines = [
        Inline(
            Order.items,
            fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
        )
    ]

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return field.name != "items.unit_price" or role_of(request) == "manager"


EDIT_FORM = {
    "customer": "1",
    "status": "SHIPPED",
    "note": "",
    "created_at": "2026-09-01T10:30",
}


def lines_form(*rows: dict[str, str]) -> dict[str, str]:
    """The order's form with these lines, as the browser would send it."""
    data = {**EDIT_FORM, "items-count": str(len(rows))}
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"items-{index}-{key}"] = value
    return data


async def first_line(database: Database) -> OrderItem:
    """The first line of the first order, as the database holds it."""
    async with database.session() as session:
        found = await session.scalars(
            select(OrderItem).where(OrderItem.order_id == 1).order_by(OrderItem.id)
        )
        lines: list[OrderItem] = list(found.all())
        return lines[0]


def client_for(database: Database, view: type[ModelView[Any]]) -> httpx.AsyncClient:
    """A client for an admin showing this view alone."""
    app = Starlette()
    app.mount("/admin", Admin(database, views=[view]))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestInlineFields:
    def test_the_parent_is_asked_by_the_path_from_it_on_each_page(self) -> None:
        asked: list[tuple[str, RequestAction]] = []

        class Asking(ModelView[Order]):
            fields = [Order.status]
            inlines = [Inline(Order.items, fields=[OrderItem.quantity])]

            def can_access_field(
                self, request: Any, field: BaseField, action: RequestAction
            ) -> bool:
                asked.append((field.name, action))
                return True

        child = Asking()._inline_view("items")
        child._pages.form_fields()
        child._pages.form_fields(record=OrderItem(id=1))
        child._pages.detail_fields()

        assert asked == [
            ("items.quantity", RequestAction.CREATE),
            ("items.quantity", RequestAction.EDIT),
            ("items.quantity", RequestAction.DETAIL),
        ]

    @pytest.mark.parametrize(("role", "shown"), [("staff", False), ("manager", True)])
    async def test_the_record_page_and_the_form_follow_it(
        self, database: Database, role: str, shown: bool
    ) -> None:
        headers = {"x-role": role}
        async with client_for(database, OrderLines) as client:
            record = await client.get("/admin/orders/1", headers=headers)
            form = await client.get("/admin/orders/1/edit", headers=headers)

        assert ("Unit price" in record.text) is shown
        assert ("59.00" in record.text.split(">Items<")[1]) is shown
        assert ('name="items-0-unit_price"' in form.text) is shown
        assert ('name="items-__index__-unit_price"' in form.text) is shown

    async def test_the_form_does_not_read_it_back(self, database: Database) -> None:
        line = await first_line(database)

        async with client_for(database, OrderLines) as client:
            answer = await client.post(
                "/admin/orders/1/edit",
                data=lines_form(
                    {
                        "key": str(line.id),
                        "product": str(line.product_id),
                        "quantity": "9",
                        "unit_price": "0.01",
                    }
                ),
                headers={"x-role": "staff"},
            )

        assert answer.status_code == 303
        saved = await first_line(database)
        assert saved.quantity == 9
        assert saved.unit_price == line.unit_price

    async def test_one_refused_on_existing_lines_is_left_empty_there(
        self, database: Database
    ) -> None:
        class NewLinesPriced(OrderLines):
            def can_access_field(
                self, request: Any, field: BaseField, action: RequestAction
            ) -> bool:
                return field.name != "items.unit_price" or action != RequestAction.EDIT

        async with client_for(database, NewLinesPriced) as client:
            form = await client.get("/admin/orders/1/edit")

        assert "Unit price" in form.text
        assert 'name="items-0-unit_price"' not in form.text
        assert 'name="items-__index__-unit_price"' in form.text
        assert "59.00" not in form.text.split('name="items-count"')[1]


class ShippedLinesLocked(OrderLines):
    """Orders whose lines keep their price once the order has shipped."""

    def can_access_field(
        self, request: Any, field: BaseField, action: RequestAction
    ) -> bool:
        return True

    def get_readonly_fields(self, request: Any, record: Order | None) -> list[Any]:
        if record is not None and record.status is OrderStatus.SHIPPED:
            return [Link(Order.items, OrderItem.unit_price)]
        return []


class TestInlineReadonlyFields:
    async def test_a_link_to_a_child_s_column_locks_it(
        self, database: Database
    ) -> None:
        async with client_for(database, ShippedLinesLocked) as client:
            shipped = await client.get("/admin/orders/1/edit")
            new = await client.get("/admin/orders/new")

        assert 'name="items-0-quantity"' in shipped.text
        assert 'name="items-0-unit_price"' not in shipped.text
        assert "59.00" in shipped.text
        assert 'name="items-0-unit_price"' in new.text

    async def test_the_form_does_not_read_it_back(self, database: Database) -> None:
        line = await first_line(database)

        async with client_for(database, ShippedLinesLocked) as client:
            answer = await client.post(
                "/admin/orders/1/edit",
                data=lines_form(
                    {
                        "key": str(line.id),
                        "product": str(line.product_id),
                        "quantity": "9",
                        "unit_price": "0.01",
                    }
                ),
            )

        assert answer.status_code == 303
        saved = await first_line(database)
        assert saved.quantity == 9
        assert saved.unit_price == line.unit_price
