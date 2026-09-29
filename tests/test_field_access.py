"""can_access_field keeps a field from some people on the pages it names."""

import csv
import io
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import (
    Admin,
    AdminSiteError,
    BaseField,
    Field,
    Link,
    ModelView,
    RequestAction,
)
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, OrderStatus


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


class TestEachPage:
    def test_a_field_is_kept_from_some_people(self) -> None:
        view = Orders()

        for page in (
            view.get_list_display,
            view.get_detail_fields,
            view.get_form_fields,
        ):
            assert "total" not in page("staff")
            assert "total" in page("manager")

    def test_the_edit_form_and_the_export_ask_too(self) -> None:
        view = Orders()

        assert "total" not in view.get_form_fields("staff", Order(id=1))
        assert view.exported(["id", "total"], "staff") == ("id",)
        assert view.exported(["id", "total"], "manager") == ("id", "total")

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
        view.get_list_display()
        view.get_detail_fields()
        view.get_form_fields()
        view.get_form_fields(record=Order(id=1))
        view.exported(["id"])

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

        Asking().get_list_display()

        assert names == {"id", "customer.email", "customer.name"}

    def test_a_hidden_column_is_not_offered_either(self) -> None:
        class Offered(Orders):
            fields = [Order.id, Field(Order.total, hidden_in_list=True)]

        view = Offered()

        assert view.get_column_choices("staff") == ("id",)
        assert view.get_column_choices("manager") == ("id", "total")

    def test_the_list_cannot_be_sorted_by_it(self) -> None:
        view = Orders()

        assert "total" not in view.readable_paths("staff")
        assert "total" in view.readable_paths("manager")

    def test_the_form_does_not_read_it_back(self) -> None:
        submitted = {"customer": "1", "status": "paid", "total": "0", "note": ""}

        result = Orders().parse_form(submitted, record=Order(id=1), request="staff")

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

        assert view.readonly_paths(None, shipped) == ("customer", "status")
        assert view.readonly_paths(None, pending) == ()
        result = view.parse_form(
            {"customer": "2", "status": "paid", "note": "Gift"}, record=shipped
        )
        assert result.values == {"note": "Gift"}

    def test_a_field_marked_read_only_needs_no_answer(self) -> None:
        class Fixed(ModelView[Order]):
            fields = [Order.status, Field(Order.total, read_only=True)]

        assert Fixed().readonly_paths(None, Order(id=1)) == ("total",)

    def test_a_misspelt_name_is_refused(self) -> None:
        class Misspelt(ModelView[Order]):
            def get_readonly_fields(
                self, request: Any, record: Order | None
            ) -> list[str]:
                return ["stauts"]

        with pytest.raises(AdminSiteError) as raised:
            Misspelt().readonly_paths()

        assert str(raised.value).startswith(
            "Misspelt.get_readonly_fields: Order has no column or relationship "
            '"stauts".'
        )
