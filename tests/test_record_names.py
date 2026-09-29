import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.audit import AuditLog, AuditQuery
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, OrderItem


class TicketBase(DeclarativeBase):
    pass


class Ticket(TicketBase):
    """A model that describes itself for developers only, as a dataclass does."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    secret: Mapped[str] = mapped_column(String(60))

    def __repr__(self) -> str:
        return f"Ticket(id={self.id!r}, secret={self.secret!r})"


class ItemView(ModelView[OrderItem]):
    list_display = ("id", "quantity")
    form_fields = ("quantity", "unit_price")


class TicketView(ModelView[Ticket]):
    pass


class CustomerView(ModelView[Customer]):
    pass


class NamedItemView(ModelView[OrderItem]):
    name = "named_items"
    record_title = "{quantity} pieces"


class TestTheName:
    def test_a_record_without_one_is_named_by_its_view_and_key(self) -> None:
        assert ItemView().get_record_title(OrderItem(id=12)) == "Order item #12"

    def test_a_repr_is_not_a_name(self) -> None:
        assert (
            TicketView().get_record_title(Ticket(id=3, secret="s3cr3t")) == "Ticket #3"
        )

    def test_a_model_that_names_itself_keeps_its_name(self) -> None:
        lena = Customer(id=1, name="Lena Fischer")

        assert CustomerView().get_record_title(lena) == "Lena Fischer"

    def test_a_record_title_comes_first(self) -> None:
        item = OrderItem(id=1, quantity=2)

        assert NamedItemView().get_record_title(item) == "2 pieces"

    def test_the_old_name_still_names_it(self) -> None:
        class Named(ModelView[OrderItem]):
            display_template = "{quantity} pieces"

        assert Named().get_record_title(OrderItem(id=1, quantity=2)) == "2 pieces"


class NamedCustomers(ModelView[Customer]):
    def get_record_title(self, customer: Customer, /) -> str:
        return f"{customer.name} of {customer.region}"


class LinkingOrders(ModelView[Order]):
    fields = [Order.id, Order.customer]


class TestAMethodOfItsOwn:
    async def test_it_names_the_record_on_its_page_and_where_it_is_linked(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[NamedCustomers, LinkingOrders])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/customers/1")
            listed = await client.get("/admin/orders")

        assert re.search(r"<h1[^>]*>\s*Lena Fischer of DE\s*</h1>", page.text)
        assert "Lena Fischer of DE" in listed.text


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[ItemView], audit=log, secret_key="for-the-session")
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestWhereItShows:
    async def test_the_record_page_is_headed_by_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/order_items/1")

        assert re.search(r"<h1[^>]*>\s*Order item #1\s*</h1>", page.text)
        assert "object at 0x" not in page.text

    async def test_the_row_menu_is_labelled_by_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/order_items")

        assert 'aria-label="Actions for Order item #1"' in page.text
        assert "object at 0x" not in page.text

    async def test_the_log_keeps_it(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        form = await client.get("/admin/order_items/1/edit")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        await client.post(
            "/admin/order_items/1/edit",
            data={"_csrf": token.group(1), "quantity": "3", "unit_price": "59.00"},
        )

        entry = (await log.find(AuditQuery(), limit=1))[0]
        assert entry.record_title == "Order item #1"
