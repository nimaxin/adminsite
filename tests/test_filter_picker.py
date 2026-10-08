"""A relation filter picks its records by name, as a form's link does."""

import html
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, ModelView, Permission, Statement
from adminsite.database import Database
from adminsite.filters import RelationFilter, SQLAlchemyRepository
from adminsite.filters.sql import filter_for
from tests.models import Customer, Order
from tests.support import Backend, count_queries


class OrderView(ModelView[Order]):
    fields = ["id", "status", "customer"]
    list_filters = [Order.customer]


class CustomerView(ModelView[Customer]):
    record_title = "{name}"
    searchable_fields = ("name",)


class EuropeanCustomers(CustomerView):
    """Every customer but the one in the UK."""

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region != "UK")


class ReadOnlyOrders(OrderView):
    """A list its user may read, and nothing more."""

    can_create = False
    can_edit = False


class HiddenOrders(OrderView):
    """A list its user may not see at all."""

    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        return False


def serve(database: Database, *views: type[ModelView[Any]]) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", Admin(database, views=list(views)))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(database, OrderView, CustomerView) as client:
        yield client


async def key_of(database: Database, email: str) -> int:
    """The key of the customer with this email."""
    async with database.session() as session:
        found = await session.scalar(select(Customer.id).where(Customer.email == email))
    assert found is not None
    return int(found)


def offered(answer: httpx.Response) -> list[str]:
    """The names a lookup offers, in order."""
    found = re.findall(r'<span class="min-w-0 break-words">([^<]*)</span>', answer.text)
    return [html.unescape(name) for name in found]


def chip(page: httpx.Response) -> str:
    """The text of the first filter chip above the list."""
    found = re.search(r"onclick=\"openFilters\('[^']*'\)\">([^<]*)</button>", page.text)
    assert found is not None, "no chip on the page"
    return html.unescape(found.group(1))


class TestTheDrawer:
    async def test_a_relation_filter_is_a_picker_of_records(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        drawer = page.text[page.text.index('id="filter-customer"') :]
        drawer = drawer[: drawer.index("</fieldset>")]
        assert "recordPicker(" in drawer
        assert 'hx-get="/admin/orders/filter/customer/lookup"' in drawer
        assert 'aria-label="Customer"' in drawer
        assert 'type="text" name="customer"' not in drawer

    async def test_the_picker_holds_the_records_the_filter_is_on(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        lena = await key_of(database, "lena@fischer.de")

        page = await client.get(f"/admin/orders?customer={lena}")

        held = f'picked: [{{"label": "Lena Fischer", "value": "{lena}"}}]'
        assert held in html.unescape(page.text)


class TestTheLookup:
    async def test_it_offers_the_records_by_name(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.get("/admin/orders/filter/customer/lookup")

        assert answer.status_code == 200
        assert set(offered(answer)) == {
            "Lena Fischer",
            "Marco Rossi",
            "Aisha Khan",
            "Jonas Berg",
        }

    async def test_typing_narrows_it(self, client: httpx.AsyncClient) -> None:
        answer = await client.get("/admin/orders/filter/customer/lookup?q=len")

        assert offered(answer) == ["Lena Fischer"]

    async def test_each_record_is_sent_as_its_key(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        lena = await key_of(database, "lena@fischer.de")

        answer = await client.get("/admin/orders/filter/customer/lookup?q=len")

        assert f'data-value="{lena}"' in answer.text

    async def test_a_record_the_scope_hides_is_not_offered(
        self, database: Database
    ) -> None:
        async with serve(database, OrderView, EuropeanCustomers) as client:
            answer = await client.get("/admin/orders/filter/customer/lookup")

        assert "Aisha Khan" not in offered(answer)
        assert "Lena Fischer" in offered(answer)

    async def test_seeing_the_list_is_enough(self, database: Database) -> None:
        async with serve(database, ReadOnlyOrders, CustomerView) as client:
            filtering = await client.get("/admin/orders/filter/customer/lookup")
            form = await client.get("/admin/orders/lookup/customer")

        assert filtering.status_code == 200
        assert "Lena Fischer" in offered(filtering)
        assert form.status_code == 403

    async def test_a_list_its_user_may_not_see_offers_nothing(
        self, database: Database
    ) -> None:
        async with serve(database, HiddenOrders, CustomerView) as client:
            answer = await client.get("/admin/orders/filter/customer/lookup")

        assert answer.status_code == 403
        assert "Lena Fischer" not in answer.text

    @pytest.mark.parametrize("name", ["status", "nobody"])
    async def test_only_a_relation_filter_looks_up(
        self, client: httpx.AsyncClient, name: str
    ) -> None:
        answer = await client.get(f"/admin/orders/filter/{name}/lookup")

        assert answer.status_code == 404


class TestPickingRecords:
    async def test_two_customers_list_the_orders_of_both(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        lena = await key_of(database, "lena@fischer.de")
        marco = await key_of(database, "marco@rossi.it")

        page = await client.get(f"/admin/orders?customer={lena}&customer={marco}")

        assert chip(page) == "Customer: Lena Fischer, Marco Rossi"
        rows = re.findall(r'<tr class="hover:bg-subtle', page.text)
        assert len(rows) == 4

    async def test_a_record_the_scope_hides_is_never_named(
        self, database: Database
    ) -> None:
        aisha = await key_of(database, "aisha@khan.co.uk")
        async with serve(database, OrderView, EuropeanCustomers) as client:
            page = await client.get(f"/admin/orders?customer={aisha}")

        assert "Aisha Khan" not in page.text
        assert chip(page) == f"Customer: {aisha}"

    async def test_the_records_are_named_in_one_query(self, backend: Backend) -> None:
        lena = await key_of(backend.database, "lena@fischer.de")
        marco = await key_of(backend.database, "marco@rossi.it")
        async with serve(backend.database, OrderView, CustomerView) as client:
            with count_queries(backend) as one:
                await client.get(f"/admin/orders?customer={lena}")
            with count_queries(backend) as two:
                await client.get(f"/admin/orders?customer={lena}&customer={marco}")

        assert two.count == one.count


class Library(DeclarativeBase):
    pass


class Author(Library):
    __tablename__ = "authors"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)


class Book(Library):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_code: Mapped[str] = mapped_column(ForeignKey("authors.code"))
    author: Mapped[Author] = relationship()


class TestTheKeyAFilterMatches:
    def test_it_is_the_linked_models_own_key(self) -> None:
        made = filter_for(SQLAlchemyRepository(Book), "author")

        assert isinstance(made, RelationFilter)
        assert made.key == "code"
