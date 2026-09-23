"""What the security review found, each hole kept closed by a test."""

import re
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import Select
from starlette.applications import Starlette

from adminsite import Admin, Inline, ModelView
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from adminsite.fields import ChoiceField
from tests.models import Customer, Order, OrderItem, Product, Setting
from tests.support import spare_product


def mount(admin: Admin) -> Starlette:
    app = Starlette()
    app.mount("/admin", admin)
    return app


def client_for(admin: Admin) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mount(admin)), base_url="http://testserver"
    )


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


def order_of_rows(page: httpx.Response) -> list[str]:
    body = page.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return re.findall(r'href="/admin/orders/(\d+)"', body)


class GermanCustomers(ModelView, model=Customer):
    """Only German customers may be seen, and so only they may be linked."""

    display_template = "{name}"

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Customer.region == "DE")


class OneProduct(ModelView, model=Product):
    display_template = "{name}"

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Product.id == 1)


class OrderView(ModelView, model=Order):
    list_display = ("id", "status")
    # A default order, so an ignored sort falls back to something definite.
    ordering = ("id",)
    exclude = ("note",)
    form_fields = ("customer", "status")
    inlines = (Inline("items", fields=("product", "quantity", "unit_price")),)


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView, GermanCustomers, OneProduct],
        secret_key="for-the-session",
    )
    async with client_for(admin) as client:
        yield client


class TestSortingFromTheUrl:
    async def test_a_column_kept_off_the_view_does_not_sort(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            for number, note in ((1, "zz"), (2, "mm"), (3, "aa")):
                order = await session.get(Order, number)
                assert order is not None
                order.note = note
            await session.commit()

        plain = await client.get("/admin/orders?sort=id")
        by_note = await client.get("/admin/orders?sort=note")

        assert order_of_rows(by_note) == order_of_rows(plain)

    async def test_a_column_on_show_still_sorts(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?sort=-id")

        assert order_of_rows(page)[:2] == ["7", "6"]

    async def test_a_column_of_another_model_does_not_sort(
        self, client: httpx.AsyncClient
    ) -> None:
        plain = await client.get("/admin/orders?sort=id")
        by_link = await client.get("/admin/orders?sort=customer.email")

        assert order_of_rows(by_link) == order_of_rows(plain)


class TestLinkingARecordOutOfScope:
    async def test_the_form_refuses_it(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        answer = await client.post(
            "/admin/orders/1/edit",
            data={"_csrf": token_in(form), "customer": "2", "status": "PAID"},
        )

        assert answer.status_code == 422
        assert "Choose a record." in answer.text
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.customer_id == 1

    async def test_it_says_nothing_about_whether_the_record_exists(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/orders/1/edit")
        token = token_in(form)

        hidden = await client.post(
            "/admin/orders/1/edit",
            data={"_csrf": token, "customer": "2", "status": "PAID"},
        )
        missing = await client.post(
            "/admin/orders/1/edit",
            data={"_csrf": token, "customer": "999", "status": "PAID"},
        )

        assert hidden.status_code == missing.status_code == 422
        for page in (hidden, missing):
            assert page.text.count("Choose a record.") == 1
            assert "999" not in page.text.split("Choose a record.")[0][-200:]

    async def test_a_record_in_scope_is_linked(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/orders/3/edit")

        answer = await client.post(
            "/admin/orders/3/edit",
            data={"_csrf": token_in(form), "customer": "1", "status": "PAID"},
        )

        assert answer.status_code == 303
        async with database.session() as session:
            order = await session.get(Order, 3)
            assert order is not None
            assert order.customer_id == 1

    async def test_an_inline_row_is_checked_too(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        answer = await client.post(
            "/admin/orders/1/edit",
            data={
                "_csrf": token_in(form),
                "customer": "1",
                "status": "SHIPPED",
                "items-count": "1",
                "items-0-key": "",
                "items-0-product": "2",
                "items-0-quantity": "1",
                "items-0-unit_price": "24.00",
            },
        )

        assert answer.status_code == 422
        assert "Choose a record." in answer.text

    async def test_the_api_refuses_it_as_well(self, database: Database) -> None:
        admin = Admin(
            database, views=[OrderView, GermanCustomers, OneProduct], api=True
        )
        async with client_for(admin) as client:
            answer = await client.patch("/admin/-/api/orders/1", json={"customer": "2"})

        assert answer.status_code == 422
        assert answer.json()["errors"] == {"customer": "Choose a record."}


class TestAnActionLeftOut:
    async def test_it_cannot_be_run_by_name(self, database: Database) -> None:
        class ShyOrders(ModelView, model=Order):
            name = "shy_orders"
            list_display = ("id",)

            @action("Purge", dangerous=True)
            async def purge(self, selection: Selection) -> str:
                return "gone"

            def get_actions(self, request: Any = None) -> tuple[Any, ...]:
                return ()

        admin = Admin(database, views=[ShyOrders], secret_key="a-secret")
        async with client_for(admin) as client:
            page = await client.get("/admin/shy_orders")

            answer = await client.post(
                "/admin/shy_orders/action/purge",
                data={"_csrf": token_in(page), "keys": ["1"]},
            )

        assert answer.status_code == 404


class TestAnAdminWithoutASession:
    """No secret key means no session, and so no form token to check."""

    async def test_a_post_from_another_site_is_refused(
        self, database: Database
    ) -> None:
        key = await spare_product(database)
        admin = Admin(database, views=[ProductView])
        async with client_for(admin) as client:
            answer = await client.post(
                f"/admin/products/{key}/delete",
                headers={"Sec-Fetch-Site": "cross-site"},
            )
            assert answer.status_code == 403

            answer = await client.post(
                f"/admin/products/{key}/delete",
                headers={"Origin": "http://evil.example"},
            )
            assert answer.status_code == 403

            assert (await client.get(f"/admin/products/{key}/edit")).status_code == 200

    async def test_a_post_from_this_site_goes_through(self, database: Database) -> None:
        key = await spare_product(database)
        admin = Admin(database, views=[ProductView])
        async with client_for(admin) as client:
            answer = await client.post(
                f"/admin/products/{key}/delete",
                headers={
                    "Sec-Fetch-Site": "same-origin",
                    "Origin": "http://testserver",
                },
            )

        assert answer.status_code == 303

    async def test_a_post_with_no_word_from_a_browser_goes_through(
        self, database: Database
    ) -> None:
        key = await spare_product(database)
        admin = Admin(database, views=[ProductView])
        async with client_for(admin) as client:
            answer = await client.post(f"/admin/products/{key}/delete")

        assert answer.status_code == 303


class ProductView(ModelView, model=Product):
    form_fields = ("name", "price")


class TestSigningIn:
    async def test_it_starts_a_fresh_session(self, database: Database) -> None:
        admin = Admin(
            database,
            views=[ProductView],
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            secret_key="a-secret",
        )
        async with client_for(admin) as client:
            login = await client.get("/admin/login")
            before = token_in(login)
            answer = await client.post(
                "/admin/login",
                data={"username": "nima", "password": "letmein", "_csrf": before},
            )
            assert answer.status_code == 303

            stale = await client.post("/admin/logout", data={"_csrf": before})
            assert stale.status_code == 403

            page = await client.get("/admin/products")
            fresh = token_in(page)
            assert fresh != before
            assert (
                await client.post("/admin/logout", data={"_csrf": fresh})
            ).status_code == 303


class TestTheExport:
    async def test_a_cell_that_would_run_as_a_formula_is_text(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            lena = await session.get(Customer, 1)
            assert lena is not None
            lena.name = '=HYPERLINK("http://evil.example","Lena")'
            await session.commit()

        class CustomerView(ModelView, model=Customer):
            list_display = ("id", "name")

        admin = Admin(database, views=[CustomerView])
        async with client_for(admin) as client:
            answer = await client.get("/admin/customers/export")

        assert "\"'=HYPERLINK" in answer.text
        assert '\n1,"=HYPERLINK' not in answer.text

    async def test_a_number_below_zero_is_left_alone(self, database: Database) -> None:
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            order.total = Decimal("-5.00")
            await session.commit()

        class Orders(ModelView, model=Order):
            list_display = ("id", "total")

        admin = Admin(database, views=[Orders])
        async with client_for(admin) as client:
            answer = await client.get("/admin/orders/export")

        assert "\n1,-5.00\n" in answer.text


class TestAPathThatNamesNoField:
    async def test_it_is_a_404_not_a_crash(self, client: httpx.AsyncClient) -> None:
        for path in ("/admin/-/files/orders/nope/x", "/admin/orders/lookup/nope"):
            answer = await client.get(path)

            assert answer.status_code == 404, path


class TestRecordActionButtons:
    async def test_the_markup_survives_the_key(self, database: Database) -> None:
        class Orders(ModelView, model=Order):
            name = "acting_orders"
            list_display = ("id",)

            @action("Confirm", on="record")
            async def confirm(self, record: Any, session: Any) -> str:
                return "ok"

        admin = Admin(database, views=[Orders])
        async with client_for(admin) as client:
            page = await client.get("/admin/acting_orders")

        assert 'onclick=\'runRecordAction("confirm", "1")\'' in page.text


class TestTheApiWritesAMultiSelect:
    async def test_a_list_of_options_is_stored(self, database: Database) -> None:
        class SettingView(ModelView, model=Setting):
            form_fields = ("name", "notes")
            fields = (
                ChoiceField(
                    "notes",
                    choices=(("a", "A"), ("b", "B"), ("c", "C")),
                    multiple=True,
                ),
            )

        admin = Admin(database, views=[SettingView], api=True)
        async with client_for(admin) as client:
            answer = await client.patch(
                "/admin/-/api/settings/1", json={"notes": ["a", "c"]}
            )

        assert answer.status_code == 200, answer.text
        async with database.session() as session:
            setting = await session.get(Setting, 1)
            assert setting is not None
            stored: Any = setting.notes
            assert stored == ["a", "c"]


class TestInlineRowsStillWork:
    async def test_a_row_in_scope_is_added(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        answer = await client.post(
            "/admin/orders/1/edit",
            data={
                "_csrf": token_in(form),
                "customer": "1",
                "status": "SHIPPED",
                "items-count": "1",
                "items-0-key": "",
                "items-0-product": "1",
                "items-0-quantity": "4",
                "items-0-unit_price": "59.00",
            },
        )

        assert answer.status_code == 303
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            await session.refresh(order, ["items"])
            assert any(
                item.product_id == 1 and item.quantity == 4 for item in order.items
            )


__all__ = ["OrderItem"]
