from typing import Any

import httpx
import pytest
from sqlalchemy import Select, select
from starlette.applications import Starlette

from adminsite import Admin
from adminsite.actions import Selection, action
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import PermissionDeniedError
from adminsite.security import Permission
from adminsite.views import ModelView
from tests.models import Customer, Order, OrderStatus


class GermanOrders(ModelView, model=Order):
    """Only shows orders from customers in one region."""

    list_display = ("id", "customer.name", "status")

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        region = request or "DE"
        return statement.where(Order.customer.has(Customer.region == region))


class ReadOnlyOrders(ModelView, model=Order):
    can_create = False
    can_edit = False
    can_delete = False


class ByRole(ModelView, model=Order):
    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        if action == Permission.DELETE:
            return bool(request == "manager")
        if action == Permission.EDIT and record is not None:
            return bool(record.status != "shipped")
        return True


class HidesAColumn(ModelView, model=Customer):
    list_display = ("name", "email", "region")

    def get_list_display(self, request: Any = None) -> tuple[str, ...]:
        if request == "support":
            return ("name", "region")
        return super().get_list_display(request)

    def get_readonly_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        return ("email",) if request == "support" else ()


class TestRowScope:
    async def test_the_list_only_shows_rows_in_scope(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view.fetch_page(session, view.build_spec())

            assert len(page) == 2
            assert {row.customer.name for row in page} == {"Lena Fischer"}

    async def test_the_total_counts_only_rows_in_scope(
        self, database: Database
    ) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view.fetch_page(session, view.build_spec().replace(limit=1))

            assert page.total == 2

    async def test_the_scope_follows_the_request(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view.fetch_page(session, view.build_spec(), request="IT")

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

            assert await view.fetch_record(session, italian.id) is None

    async def test_a_row_inside_the_scope_opens(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            page = await view.fetch_page(session, view.build_spec())
            wanted = page.rows[0]

            assert await view.fetch_record(session, wanted.id) is not None

    async def test_the_scope_also_narrows_the_search(self, database: Database) -> None:
        view = GermanOrders()
        async with database.session() as session:
            spec = view.build_spec(search="rossi")
            spec = spec.replace(search_paths=("customer.name",))

            page = await view.fetch_page(session, spec)

            assert len(page) == 0


class TestActionPermissions:
    async def test_flags_refuse_the_action(self, database: Database) -> None:
        view = ReadOnlyOrders()

        assert await view.allows(Permission.VIEW) is True
        assert await view.allows(Permission.CREATE) is False
        assert await view.allows(Permission.EDIT) is False
        assert await view.allows(Permission.DELETE) is False

    async def test_saving_is_refused_when_creating_is_not_allowed(
        self, database: Database
    ) -> None:
        view = ReadOnlyOrders()
        async with database.session() as session:
            with pytest.raises(PermissionDeniedError, match="You cannot create"):
                await view.save(session, {"note": "new"})

    async def test_deleting_is_refused(self, database: Database) -> None:
        view = ReadOnlyOrders()
        async with database.session() as session:
            record = await session.scalar(select(Order))
            assert record is not None

            with pytest.raises(PermissionDeniedError, match="You cannot delete"):
                await view.delete(session, record)

    async def test_permission_can_depend_on_the_user(self, database: Database) -> None:
        view = ByRole()

        assert await view.allows(Permission.DELETE, request="manager") is True
        assert await view.allows(Permission.DELETE, request="support") is False

    async def test_permission_can_depend_on_the_record(
        self, database: Database
    ) -> None:
        view = ByRole()
        shipped = Order(status="shipped")
        pending = Order(status="pending")

        assert await view.allows(Permission.EDIT, record=shipped) is False
        assert await view.allows(Permission.EDIT, record=pending) is True

    async def test_a_refused_delete_leaves_the_record(self, database: Database) -> None:
        view = ByRole()
        async with database.session() as session:
            record = await session.scalar(select(Order))
            assert record is not None
            key = record.id

            with pytest.raises(PermissionDeniedError):
                await view.delete(session, record, request="support")

            assert await session.get(Order, key) is not None


class TestFieldPermissions:
    def test_a_column_can_be_hidden_from_some_people(self) -> None:
        view = HidesAColumn()

        assert view.get_list_display("support") == ("name", "region")
        assert view.get_list_display("manager") == ("name", "email", "region")

    def test_a_field_can_be_locked_for_some_people(self) -> None:
        view = HidesAColumn()

        assert view.get_readonly_fields("support") == ("email",)
        assert view.get_readonly_fields("manager") == ()

    def test_a_locked_field_is_ignored_when_the_form_comes_back(self) -> None:
        view = HidesAColumn()

        result = view.parse_form(
            {"name": "Lena", "email": "changed@example.com", "region": "DE"},
            request="support",
        )

        assert "email" not in result.values
        assert result.values["name"] == "Lena"


class TestButtonsFollowPermissions:
    async def test_actions_the_user_may_not_run_are_left_out(
        self, database: Database
    ) -> None:
        class GuardedOrders(ModelView, model=Order):
            name = "guarded"

            @action("Mark as shipped")
            async def ship(self, selection: Selection) -> str:
                return "done"

            @action("Discard", permission=Permission.DELETE)
            async def discard(self, selection: Selection) -> str:
                return "gone"

            async def allows(
                self,
                action: Permission | str,
                *,
                request: Any = None,
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
        class NoDeletingShipped(ModelView, model=Order):
            name = "careful"

            async def allows(
                self,
                action: Permission | str,
                *,
                request: Any = None,
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


def client_for(database: Database, view: type[ModelView]) -> httpx.AsyncClient:
    site = Admin(database, title="Shop")
    site.add_view(view)
    app = Starlette()
    app.mount("/admin", site)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
