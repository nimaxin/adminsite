import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Select
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from adminsite.fields import ChoiceField
from tests.models import Customer, Order, Product


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total")
    list_filter = ("status",)
    search_fields = ("customer.name",)
    form_fields = ("customer", "status", "total", "note", "created_at")

    @action(
        "Add a note",
        inputs=[ChoiceField("tone", choices=(("kind", "Kind"),), required=True)],
    )
    async def add_note(self, selection: Selection, tone: str) -> str:
        changed = await selection.update(note=f"A {tone} note")
        return f"{changed} orders noted."


class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email", "region")

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Customer.region != "SE")


class ProductView(ModelView, model=Product):
    form_fields = ("name", "price")

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        if action == Permission.EDIT:
            return False
        return await super().allows(action, request=request, record=record)


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[OrderView, CustomerView, ProductView], api=True)
    async with serve(admin) as client:
        yield client


class TestSwitchingOn:
    async def test_the_api_is_off_unless_asked_for(self, database: Database) -> None:
        async with serve(Admin(database, views=[OrderView])) as client:
            answer = await client.get("/admin/-/api/orders")

        assert answer.status_code == 404

    async def test_the_index_describes_the_views(
        self, client: httpx.AsyncClient
    ) -> None:
        views = (await client.get("/admin/-/api")).json()["views"]
        orders = next(view for view in views if view["name"] == "orders")

        assert [field["path"] for field in orders["fields"]][:3] == [
            "id",
            "customer.name",
            "status",
        ]
        assert orders["actions"] == [
            {"name": "add_note", "label": "Add a note"},
            {"name": "delete_selected", "label": "Delete"},
        ]


class TestReading:
    async def test_a_page_of_records(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/admin/-/api/orders?sort=id&limit=2")).json()

        assert body["total"] == 7
        assert body["has_next"] is True
        first = body["items"][0]
        assert first["key"] == "1"
        assert first["customer"] == "1"
        assert first["customer.name"] == "Lena Fischer"
        assert first["status"] == "shipped"
        assert first["total"] == "107.00"
        assert first["created_at"] == "2026-09-01T10:30:00"

    async def test_filters_search_and_pages(self, client: httpx.AsyncClient) -> None:
        shipped = (await client.get("/admin/-/api/orders?status=SHIPPED")).json()
        lena = (await client.get("/admin/-/api/orders?q=lena")).json()
        second = (await client.get("/admin/-/api/orders?sort=id&limit=3&page=2")).json()

        assert shipped["total"] == 2
        assert lena["total"] == 2
        assert [item["key"] for item in second["items"]] == ["4", "5", "6"]

    async def test_one_record(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/admin/-/api/orders/3")).json()

        assert body["key"] == "3"
        assert body["status"] == "pending"

    async def test_the_scope_applies(self, client: httpx.AsyncClient) -> None:
        listed = (await client.get("/admin/-/api/customers")).json()
        hidden = await client.get("/admin/-/api/customers/4")

        assert listed["total"] == 3
        assert hidden.status_code == 404
        assert hidden.json() == {"error": "No such record."}


class TestWriting:
    async def test_adding_a_record(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/admin/-/api/customers",
            json={"name": "Mia", "email": "mia@x.nl", "region": "NL"},
        )

        assert answer.status_code == 201
        assert answer.json()["name"] == "Mia"
        assert answer.json()["key"] == "5"

    async def test_problems_come_back_per_field(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/orders",
            json={"customer": 1, "status": "lost", "total": "abc", "colour": "red"},
        )

        assert answer.status_code == 422
        errors = answer.json()["errors"]
        assert set(errors) == {"status", "total", "colour", "created_at"}
        assert errors["colour"] == "This field cannot be written."

    async def test_a_patch_changes_only_what_was_sent(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.patch("/admin/-/api/orders/1", json={"note": "Gift"})

        assert answer.status_code == 200
        assert answer.json()["note"] == "Gift"
        assert answer.json()["status"] == "shipped"

    async def test_editing_needs_the_permission(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.patch("/admin/-/api/products/1", json={"name": "X"})

        assert answer.status_code == 403
        assert answer.json() == {"error": "You cannot edit Products."}

    async def test_deleting(self, client: httpx.AsyncClient) -> None:
        answer = await client.delete("/admin/-/api/orders/7")
        again = await client.get("/admin/-/api/orders/7")

        assert answer.status_code == 204
        assert again.status_code == 404

    async def test_a_delete_other_records_need_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.delete("/admin/-/api/products/1")

        assert answer.status_code == 409

    async def test_the_body_has_to_be_an_object(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/customers",
            content=b"[1, 2]",
            headers={"content-type": "application/json"},
        )

        assert answer.status_code == 400


class TestActions:
    async def test_an_action_runs_over_keys(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.post(
            "/admin/-/api/orders/actions/add_note",
            json={"keys": ["1", "2"], "inputs": {"tone": "kind"}},
        )

        assert answer.json() == {"message": "2 orders noted."}
        async with database.session() as session:
            order = await session.get(Order, 2)
            assert order is not None
            assert order.note == "A kind note"

    async def test_an_action_over_everything_that_matches(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/orders/actions/add_note?status=PENDING",
            json={"everything": True, "inputs": {"tone": "kind"}},
        )

        assert answer.json() == {"message": "2 orders noted."}

    async def test_missing_inputs_are_named(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/admin/-/api/orders/actions/add_note", json={"keys": ["1"]}
        )

        assert answer.status_code == 422
        assert "tone" in answer.json()["errors"]


class TokenAuth(PasswordAuth):
    async def authenticate_token(self, token: str) -> Any | None:
        return "robot" if token == "s3cret" else None


@pytest.fixture
async def guarded(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView],
        api=True,
        auth=TokenAuth({"nima": hash_password("letmein")}),
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


class TestSigningIn:
    async def test_nobody_gets_in_without_signing_in(
        self, guarded: httpx.AsyncClient
    ) -> None:
        answer = await guarded.get("/admin/-/api/orders")

        assert answer.status_code == 401
        assert answer.headers["www-authenticate"] == "Bearer"

    async def test_a_token_works_without_the_form_token(
        self, guarded: httpx.AsyncClient
    ) -> None:
        headers = {"Authorization": "Bearer s3cret"}
        listed = await guarded.get("/admin/-/api/orders", headers=headers)
        changed = await guarded.patch(
            "/admin/-/api/orders/1", json={"note": "x"}, headers=headers
        )
        wrong = await guarded.get(
            "/admin/-/api/orders", headers={"Authorization": "Bearer nope"}
        )

        assert listed.status_code == 200
        assert changed.status_code == 200
        assert wrong.status_code == 401

    async def test_a_session_needs_the_form_token_to_change_things(
        self, guarded: httpx.AsyncClient
    ) -> None:
        login = await guarded.get("/admin/login")
        token = re.search(r'name="_csrf" value="([^"]+)"', login.text)
        assert token is not None
        await guarded.post(
            "/admin/login",
            data={"username": "nima", "password": "letmein", "_csrf": token.group(1)},
        )

        # Signing in starts a fresh session, so the token comes from a page
        # drawn afterwards, as it would for a script run from the admin.
        page = await guarded.get("/admin/orders")
        fresh = re.search(r'name="_csrf" value="([^"]+)"', page.text)
        assert fresh is not None

        listed = await guarded.get("/admin/-/api/orders")
        refused = await guarded.patch("/admin/-/api/orders/1", json={"note": "x"})
        allowed = await guarded.patch(
            "/admin/-/api/orders/1",
            json={"note": "x"},
            headers={"X-CSRF-Token": fresh.group(1)},
        )

        assert listed.status_code == 200
        assert refused.status_code == 403
        assert allowed.status_code == 200
