from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, BaseField, Statement
from adminsite.actions import Selection, action
from adminsite.database import Database
from adminsite.exceptions import PermissionDeniedError
from adminsite.permissions import Permission, RequestAction
from adminsite.views import ModelView
from tests.models import Customer, Order, OrderStatus
from tests.support import request_from


class GermanOrders(ModelView[Order]):
    """Only shows orders from customers in one region."""

    fields = ["id", "customer.name", "status"]

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        region = request.scope.get("user_record") or "DE"
        return statement.where(Order.customer.has(Customer.region == region))


class GermanOrdersByStatus(GermanOrders):
    list_filters = [Order.status]


class ReadOnlyOrders(ModelView[Order]):
    can_create = False
    can_edit = False
    can_delete = False


class ByRole(ModelView[Order]):
    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        if action == Permission.DELETE:
            return bool(request.scope.get("user_record") == "manager")
        if action == Permission.EDIT and record is not None:
            return bool(record.status != "shipped")
        return True


class HidesAColumn(ModelView[Customer]):
    fields = ["name", "email", "region"]

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        hidden = (
            request.scope.get("user_record") == "support"
            and action is RequestAction.LIST
        )
        return not (hidden and field.name == "email")

    def get_readonly_fields(
        self, request: Request, record: Any = None
    ) -> tuple[str, ...]:
        return ("email",) if request.scope.get("user_record") == "support" else ()


class TestRowScope:
    async def test_the_list_only_shows_rows_in_scope(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view._reader.fetch_page(
                session,
                view._reader.build_spec(request=request_from()),
                request=request_from(),
            )

            assert len(page) == 2
            assert {row.customer.name for row in page} == {"Lena Fischer"}

    async def test_the_total_counts_only_rows_in_scope(
        self, database: Database
    ) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view._reader.fetch_page(
                session,
                view._reader.build_spec(request=request_from()).replace(limit=1),
                request=request_from(),
            )

            assert page.total == 2

    async def test_the_scope_follows_the_request(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view._reader.fetch_page(
                session,
                view._reader.build_spec(request=request_from()),
                request=request_from("IT"),
            )

            assert {row.customer.name for row in page} == {"Marco Rossi"}

    async def test_a_row_outside_the_scope_reads_as_missing(
        self, database: Database
    ) -> None:
        view = GermanOrders()
        async with database.session() as session:
            italian = await session.scalar(
                select(Order).join(Customer).where(Customer.region == "IT").limit(1)
            )
            assert italian is not None

            assert (
                await view._reader.fetch_record(
                    session, italian.id, request=request_from()
                )
                is None
            )

    async def test_a_row_inside_the_scope_opens(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view._reader.fetch_page(
                session,
                view._reader.build_spec(request=request_from()),
                request=request_from(),
            )
            wanted = page.rows[0]

            assert (
                await view._reader.fetch_record(
                    session, wanted.id, request=request_from()
                )
                is not None
            )

    async def test_filter_counts_only_count_rows_in_scope(
        self, database: Database
    ) -> None:
        """A count of every row would say how many the user may not see."""
        view = GermanOrdersByStatus()
        async with database.session() as session:
            ((_status, options),) = await view._reader.filter_options(
                session,
                view._reader.build_spec(request=request_from()),
                request=request_from(),
            )

            counts = {option.value: option.count for option in options}
            assert counts == {
                "PENDING": None,
                "PAID": 1,
                "SHIPPED": 1,
                "REFUNDED": None,
            }

    async def test_the_scope_also_narrows_the_search(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            spec = view._reader.build_spec(search="rossi", request=request_from())
            spec = spec.replace(search_paths=("customer.name",))

            page = await view._reader.fetch_page(session, spec, request=request_from())

            assert len(page) == 0


class TestActionPermissions:
    async def test_flags_refuse_the_action(self, database: Database) -> None:
        view = ReadOnlyOrders()

        assert (
            await view.allows(Permission.VIEW, request=request_from(), record=None)
            is True
        )
        assert (
            await view.allows(Permission.CREATE, request=request_from(), record=None)
            is False
        )
        assert (
            await view.allows(Permission.EDIT, request=request_from(), record=None)
            is False
        )
        assert (
            await view.allows(Permission.DELETE, request=request_from(), record=None)
            is False
        )

    async def test_saving_is_refused_when_creating_is_not_allowed(
        self, database: Database
    ) -> None:
        view = ReadOnlyOrders()
        async with database.session() as session:
            with pytest.raises(PermissionDeniedError, match="You cannot create"):
                await view._saver.save(session, {"note": "new"}, request=request_from())

    async def test_deleting_is_refused(self, database: Database) -> None:
        view = ReadOnlyOrders()
        async with database.session() as session:
            record = await session.scalar(select(Order))
            assert record is not None

            with pytest.raises(PermissionDeniedError, match="You cannot delete"):
                await view._saver.delete(session, record, request=request_from())

    async def test_permission_can_depend_on_the_user(self, database: Database) -> None:
        view = ByRole()

        assert (
            await view.allows(Permission.DELETE, request=request_from("manager"))
            is True
        )
        assert (
            await view.allows(Permission.DELETE, request=request_from("support"))
            is False
        )

    async def test_permission_can_depend_on_the_record(
        self, database: Database
    ) -> None:
        view = ByRole()
        shipped = Order(status="shipped")
        pending = Order(status="pending")

        assert (
            await view.allows(Permission.EDIT, record=shipped, request=request_from())
            is False
        )
        assert (
            await view.allows(Permission.EDIT, record=pending, request=request_from())
            is True
        )

    async def test_a_refused_delete_leaves_the_record(self, database: Database) -> None:
        view = ByRole()
        async with database.session() as session:
            record = await session.scalar(select(Order))
            assert record is not None
            key = record.id

            with pytest.raises(PermissionDeniedError):
                await view._saver.delete(
                    session, record, request=request_from("support")
                )

            assert await session.get(Order, key) is not None


class TestFieldPermissions:
    def test_a_column_can_be_hidden_from_some_people(self) -> None:
        view = HidesAColumn()

        assert view._pages.list_fields(request_from("support")) == ("name", "region")
        assert view._pages.list_fields(request_from("manager")) == (
            "name",
            "email",
            "region",
        )

    def test_a_field_can_be_locked_for_some_people(self) -> None:
        view = HidesAColumn()

        assert view.get_readonly_fields(request_from("support")) == ("email",)
        assert view.get_readonly_fields(request_from("manager")) == ()

    def test_a_locked_field_is_ignored_when_the_form_comes_back(self) -> None:
        view = HidesAColumn()

        result = view._forms.parse(
            {"name": "Lena", "email": "changed@example.com", "region": "DE"},
            request=request_from("support"),
        )

        assert "email" not in result.values
        assert result.values["name"] == "Lena"


class TestButtonsFollowPermissions:
    async def test_actions_the_user_may_not_run_are_left_out(
        self, database: Database
    ) -> None:
        class GuardedOrders(ModelView[Order]):
            name = "guarded"

            @action("Mark as shipped")
            async def ship(self, selection: Selection[Order]) -> str:
                return "done"

            @action("Discard", permission=Permission.DELETE)
            async def discard(self, selection: Selection[Order]) -> str:
                return "gone"

            async def allows(
                self,
                action: Permission | str,
                *,
                request: Request,
                record: Any = None,
            ) -> bool:
                return action != Permission.DELETE

        client = client_for(database, GuardedOrders)
        response = await client.get("/admin/guarded")

        assert "Mark as shipped" in response.text
        assert "Discard" not in response.text

    async def test_the_delete_button_follows_the_record(
        self, database: Database
    ) -> None:
        class NoDeletingShipped(ModelView[Order]):
            name = "careful"

            async def allows(
                self,
                action: Permission | str,
                *,
                request: Request,
                record: Any = None,
            ) -> bool:
                if action == Permission.DELETE and record is not None:
                    return bool(record.status != OrderStatus.SHIPPED)
                return True

        client = client_for(database, NoDeletingShipped)
        shipped = await client.get("/admin/careful/1/edit")
        pending = await client.get("/admin/careful/3/edit")

        assert 'id="confirm-delete"' not in shipped.text
        assert 'id="confirm-delete"' in pending.text


def client_for(database: Database, view: type[ModelView[Any]]) -> httpx.AsyncClient:
    site = Admin(database, title="Shop")
    site.add_view(view)
    app = Starlette()
    app.mount("/admin", site)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
