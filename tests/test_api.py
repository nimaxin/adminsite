import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from adminsite import Admin, Field, ModelView, Permission, Statement
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from adminsite.fields import EnumField
from tests.models import Customer, Order, OrderStatus, Product
from tests.support import REFUSED

refunded: list[Order] = []


class OrderView(ModelView[Order]):
    fields = [
        "id",
        "customer.name",
        "customer",
        "status",
        "total",
        "note",
        "created_at",
    ]
    exclude_fields_from_list = ["customer", "note", "created_at"]
    list_filters = ("status",)
    searchable_fields = ("customer.name",)

    @action(
        "Add a note",
        inputs=[EnumField("tone", choices=(("kind", "Kind"),), required=True)],
    )
    async def add_note(self, selection: Selection[Order], tone: str) -> str:
        changed = await selection.update(note=f"A {tone} note")
        return f"{changed} orders noted."


class CustomerView(ModelView[Customer]):
    fields = ["name", "email", "region"]

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region != "SE")

    @action("Give the same email")
    async def same_email(self, selection: Selection[Customer]) -> str:
        return f"{await selection.update(email='shared@example.com')} changed."


class ProductView(ModelView[Product]):
    fields = ["name", "price"]

    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        if action == Permission.EDIT:
            return False
        return await super().allows(action, request=request, record=record)

    @action("Remove", permission=Permission.DELETE)
    async def remove(self, selection: Selection[Product]) -> str:
        return f"{await selection.delete()} removed."


class RefundView(ModelView[Order]):
    """Refunds one order at a time, only once it is paid."""

    name = "refunds"
    fields = ["id", "status"]

    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        if action == Permission.EDIT and record is not None:
            return bool(record.status is OrderStatus.PAID)
        return await super().allows(action, request=request, record=record)

    @action("Refund", on="record")
    async def refund(self, order: Order) -> str:
        refunded.append(order)
        return f"Refunded {type(order).__name__} {order.id}."

    @action("Receipt", on="record", permission=Permission.VIEW)
    async def receipt(self, order: Order) -> Response:
        return Response(f"order {order.id}", media_type="application/zip")


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    refunded.clear()
    admin = Admin(
        database, views=[OrderView, CustomerView, ProductView, RefundView], api=True
    )
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
            {"name": "add_note", "label": "Add a note", "on": "selection"},
            {"name": "delete_selected", "label": "Delete", "on": "selection"},
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
        assert first["created_at"] == "2026-09-01T10:30:00Z"

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

    async def test_a_field_only_a_new_record_takes_is_not_read(
        self, database: Database
    ) -> None:
        class SetOnce(ModelView[Order]):
            name = "set_once"
            fields = [
                Order.id,
                Order.status,
                Field(
                    Order.note,
                    exclude_from_list=True,
                    exclude_from_detail=True,
                    exclude_from_edit=True,
                ),
            ]

        async with serve(Admin(database, views=[SetOnce], api=True)) as client:
            record = (await client.get("/admin/-/api/set_once/1")).json()
            listed = (await client.get("/admin/-/api/set_once")).json()
            views = (await client.get("/admin/-/api")).json()["views"]

        assert "note" not in record
        assert "note" not in listed["items"][0]
        # The index still says a new record takes it.
        assert "note" in [field["path"] for field in views[0]["fields"]]


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

    async def test_a_record_saved_out_of_the_scope_answers_with_its_key(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        changed = await client.patch("/admin/-/api/customers/1", json={"region": "SE"})
        added = await client.post(
            "/admin/-/api/customers",
            json={"name": "Mia", "email": "mia@x.se", "region": "SE"},
        )

        assert (changed.status_code, changed.json()) == (200, {"key": "1"})
        assert (added.status_code, added.json()) == (201, {"key": "5"})
        async with database.session() as session:
            lena = await session.get(Customer, 1)
            assert lena is not None
            assert lena.region == "SE"

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

    async def test_a_change_the_database_refuses_is_a_conflict(
        self, client: httpx.AsyncClient
    ) -> None:
        # Order lines still point at the first product.
        answer = await client.post(
            "/admin/-/api/products/actions/remove", json={"keys": ["1"]}
        )

        assert answer.status_code == 409
        assert answer.json() == {"error": REFUSED}

    async def test_a_value_taken_twice_is_not_blamed_on_references(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/customers/actions/same_email", json={"keys": ["1", "2"]}
        )

        assert answer.status_code == 409
        assert answer.json() == {"error": REFUSED}

    async def test_missing_inputs_are_named(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/admin/-/api/orders/actions/add_note", json={"keys": ["1"]}
        )

        assert answer.status_code == 422
        assert "tone" in answer.json()["errors"]

    async def test_inputs_have_to_be_an_object(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/admin/-/api/orders/actions/add_note",
            json={"keys": ["1"], "inputs": ["kind"]},
        )

        assert answer.status_code == 422
        assert answer.json() == {"error": "inputs is an object."}

    async def test_the_index_says_what_each_action_runs_on(
        self, client: httpx.AsyncClient
    ) -> None:
        views = (await client.get("/admin/-/api")).json()["views"]
        refunds = next(view for view in views if view["name"] == "refunds")

        assert {item["name"]: item["on"] for item in refunds["actions"]} == {
            "receipt": "record",
            "refund": "record",
            "delete_selected": "selection",
        }


class TestARecordAction:
    async def test_it_is_handed_the_record(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/admin/-/api/refunds/actions/refund", json={"keys": ["2"]}
        )

        assert answer.status_code == 200
        assert answer.json() == {"message": "Refunded Order 2."}

    async def test_the_records_own_permission_decides(
        self, client: httpx.AsyncClient
    ) -> None:
        # Order 1 is shipped, so it cannot be refunded.
        answer = await client.post(
            "/admin/-/api/refunds/actions/refund", json={"keys": ["1"]}
        )

        assert answer.status_code == 403
        assert answer.json() == {"error": "You cannot edit Orders."}
        assert refunded == []

    async def test_it_takes_one_key(self, client: httpx.AsyncClient) -> None:
        several = await client.post(
            "/admin/-/api/refunds/actions/refund", json={"keys": ["2", "6"]}
        )
        none = await client.post(
            "/admin/-/api/refunds/actions/refund", json={"everything": True}
        )

        for answer in (several, none):
            assert answer.status_code == 422
            assert answer.json() == {
                "error": "This action runs on one record. Send its key."
            }
        assert refunded == []

    async def test_a_key_it_cannot_find_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/refunds/actions/refund", json={"keys": ["99"]}
        )

        assert answer.status_code == 404
        assert answer.json() == {"error": "No such record."}

    async def test_a_file_it_answers_with_is_sent(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/refunds/actions/receipt", json={"keys": ["2"]}
        )

        assert answer.status_code == 200
        assert answer.headers["content-type"] == "application/zip"
        assert answer.text == "order 2"


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

    async def test_a_view_reads_who_the_token_is_for(self, database: Database) -> None:
        class RobotOrders(ModelView[Order]):
            async def allows(
                self,
                action: Permission | str,
                *,
                request: Request,
                record: Order | None,
            ) -> bool:
                return bool(request.state.user == "robot")

        admin = Admin(
            database,
            views=[RobotOrders],
            api=True,
            auth=TokenAuth({"nima": hash_password("letmein")}),
            secret_key="for-the-session",
        )
        async with serve(admin) as client:
            answer = await client.get(
                "/admin/-/api/orders", headers={"Authorization": "Bearer s3cret"}
            )

        assert answer.status_code == 200

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
