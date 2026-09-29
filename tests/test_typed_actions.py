"""An action asks for its values with typed parameters, and is handed the rest."""

import dataclasses
import enum
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, assert_type

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.requests import Request

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Input, Selection, action
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.exceptions import AdminSiteError
from adminsite.fields import (
    BooleanField,
    DateField,
    DecimalField,
    EnumField,
    IntegerField,
    RelationField,
    StringField,
    TextAreaField,
)
from adminsite.fields.files import UploadField
from tests.models import Customer, Order, OrderStatus

seen: dict[str, Any] = {}


class Carrier(enum.Enum):
    DHL = "dhl"
    UPS = "ups"


@dataclass(frozen=True)
class Discount:
    """How much to take off the chosen orders."""

    percent: Annotated[
        Decimal, Input(label="Take off (%)", help_text="Between 0 and 100.")
    ]
    round_to: Decimal = Decimal("0.05")
    only_pending: bool = True


class OrderView(ModelView[Order]):
    fields = [Order.id, Order.status, Order.total, Order.note]
    fields_default_sort = [Order.id]

    @action("Ship", confirm="Ship the chosen orders?")
    async def ship(
        self,
        selection: Selection[Order],
        *,
        carrier: Carrier,
        tracking: str | None = None,
        when: date | None = None,
    ) -> str:
        orders = assert_type(await selection.records(), list[Order])
        for order in orders:
            order.status = OrderStatus.SHIPPED
        seen.update(carrier=carrier, tracking=tracking, when=when, orders=orders)
        return f"{len(orders)} orders shipped with {carrier.name}."

    @action("Discount")
    async def discount(self, selection: Selection[Order], *, discount: Discount) -> str:
        seen["discount"] = discount
        return "Discounted."

    @action("Note", on="record")
    async def add_note(
        self,
        order: Order,
        request: Request,
        *,
        note: Annotated[str, Input(label="Note", multiline=True)],
        copies: int = 1,
    ) -> str:
        order.note = note * copies
        seen.update(order=order, request=request, copies=copies)
        return f"Order {order.id} noted."

    @action("Hand over", on="record")
    async def hand_over(
        self, order: Order, *, customer: Annotated[Customer, Input(label="To")]
    ) -> str:
        seen["customer"] = customer
        return f"Order {order.id} handed to {customer.name}."

    @action("Read a file", on="view", permission=Permission.VIEW)
    async def read_file(
        self,
        session: SessionAdapter,
        *,
        rows: Annotated[UploadFile, Input(label="Rows", accept=".csv")],
        kind: Literal["orders", "returns"] = "orders",
    ) -> str:
        text = (await rows.read()).decode()
        seen.update(session=session, kind=kind, filename=rows.filename)
        return f"{len(text.splitlines())} rows of {kind} read."


class CustomerView(ModelView[Customer]):
    pass


class Moves(ModelView[Order]):
    """Its warehouses are worked out when the page is drawn."""

    name = "moves"

    @action("Move")
    async def move(self, selection: Selection[Order], *, warehouse: str) -> str:
        seen["warehouse"] = warehouse
        return f"Moved to {warehouse}."

    def get_actions(self, request: Any = None) -> tuple[Any, ...]:
        choices = (("north", "North"), ("south", "South"))
        return tuple(
            dataclasses.replace(item, inputs=(EnumField("warehouse", choices=choices),))
            if item.name == "move"
            else item
            for item in super().get_actions(request)
        )


class Counting(ModelView[Order]):
    """Asks for SQLAlchemy's own session, which only an async database has."""

    name = "counted"

    @action("Count", on="view", permission=Permission.VIEW)
    async def count(self, session: AsyncSession) -> str:
        total = await session.scalar(select(func.count()).select_from(Order))
        return f"Counted {total} orders."


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    seen.clear()
    admin = Admin(
        database,
        views=[OrderView, CustomerView, Moves],
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


def dialog_for(page: httpx.Response, name: str) -> str:
    return page.text.split(f'id="action-{name}"', 1)[1].split("</dialog>", 1)[0]


async def run(
    client: httpx.AsyncClient,
    name: str,
    data: dict[str, Any],
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    page = await client.get("/admin/orders")
    return await client.post(
        f"/admin/orders/action/{name}",
        data={"_csrf": token_in(page), **data},
        files=files,
        follow_redirects=True,
    )


async def order_status(database: Database, key: int) -> OrderStatus:
    async with database.session() as session:
        order = await session.get(Order, key)
        assert order is not None
        return order.status


def inputs_of(name: str) -> dict[str, Any]:
    return {item.name: item for item in OrderView().action_named(name).inputs}


class TestWhatTheDialogAsksFor:
    def test_each_parameter_it_is_not_handed(self) -> None:
        asked = inputs_of("ship")

        assert list(asked) == ["carrier", "tracking", "when"]
        assert isinstance(asked["carrier"], EnumField)
        assert asked["carrier"].enum is Carrier
        assert asked["carrier"].required is True
        assert isinstance(asked["tracking"], StringField)
        assert asked["tracking"].required is False
        assert isinstance(asked["when"], DateField)

    def test_the_record_and_the_request_are_handed_over(self) -> None:
        asked = inputs_of("add_note")

        assert list(asked) == ["note", "copies"]
        assert isinstance(asked["note"], TextAreaField)
        assert asked["note"].label == "Note"
        assert isinstance(asked["copies"], IntegerField)
        assert asked["copies"].default == 1
        assert asked["copies"].required is False

    def test_a_dataclass_is_asked_for_field_by_field(self) -> None:
        asked = inputs_of("discount")

        assert list(asked) == [
            "discount.percent",
            "discount.round_to",
            "discount.only_pending",
        ]
        percent = asked["discount.percent"]
        assert isinstance(percent, DecimalField)
        assert (percent.label, percent.help_text) == (
            "Take off (%)",
            "Between 0 and 100.",
        )
        assert percent.required is True
        assert asked["discount.round_to"].default == Decimal("0.05")
        assert isinstance(asked["discount.only_pending"], BooleanField)
        assert asked["discount.only_pending"].default is True

    def test_a_model_is_a_record_to_pick(self) -> None:
        customer = inputs_of("hand_over")["customer"]

        assert isinstance(customer, RelationField)
        assert customer.related_model is Customer
        assert customer.label == "To"
        assert customer.required is True

    def test_a_file_and_a_literal(self) -> None:
        asked = inputs_of("read_file")

        assert isinstance(asked["rows"], UploadField)
        assert asked["rows"].accept == ".csv"
        assert isinstance(asked["kind"], EnumField)
        assert asked["kind"].choices == (("orders", "Orders"), ("returns", "Returns"))
        assert asked["kind"].default == "orders"

    def test_input_may_sit_inside_or_around_none(self) -> None:
        class Notes(ModelView[Order]):
            @action("Note")
            async def note(
                self,
                selection: Selection[Order],
                *,
                inside: Annotated[str, Input(label="Inside")] | None,
                around: Annotated[str | None, Input(label="Around")],
            ) -> str:
                return ""

        asked = Notes().action_named("note").inputs

        assert [(item.label, item.required) for item in asked] == [
            ("Inside", False),
            ("Around", False),
        ]

    def test_a_list_asks_for_several(self) -> None:
        class Offers(ModelView[Order]):
            @action("Offer")
            async def offer(
                self,
                selection: Selection[Order],
                *,
                carriers: list[Carrier],
                customers: list[Customer],
            ) -> str:
                return ""

        carriers, customers = Offers().action_named("offer").inputs

        assert isinstance(carriers, EnumField)
        assert carriers.multiple is True
        assert isinstance(customers, RelationField)
        assert customers.collection is True
        assert customers.related_model is Customer


class TestTheDialog:
    async def test_a_group_sits_under_its_heading(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        dialog = dialog_for(page, "discount")
        assert "<legend" in dialog
        assert "Discount</legend>" in dialog
        assert 'name="discount.percent"' in dialog
        assert "Take off (%)" in dialog

    async def test_a_file_is_sent_with_the_form(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        dialog = dialog_for(page, "read_file")
        assert 'enctype="multipart/form-data"' in dialog
        assert 'type="file"' in dialog
        assert 'accept=".csv"' in dialog

    async def test_a_dialog_without_a_file_is_not(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert "multipart" not in dialog_for(page, "ship")

    async def test_a_default_fills_its_input(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        copies = re.search(
            r'<input[^>]*name="copies"[^>]*>', dialog_for(page, "add_note")
        )
        assert copies is not None
        assert 'value="1"' in copies.group(0)


class TestTheValuesArriveTyped:
    async def test_over_a_selection(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(
            client,
            "ship",
            {
                "keys": ["1", "2"],
                "carrier": "DHL",
                "tracking": "",
                "when": "2026-09-30",
            },
        )

        assert "2 orders shipped with DHL." in answer.text
        assert seen["carrier"] is Carrier.DHL
        assert seen["tracking"] is None
        assert seen["when"] == date(2026, 9, 30)
        assert all(isinstance(order, Order) for order in seen["orders"])
        assert await order_status(database, 2) is OrderStatus.SHIPPED

    async def test_a_group_as_its_dataclass(self, client: httpx.AsyncClient) -> None:
        await run(
            client,
            "discount",
            {"keys": ["1"], "discount.percent": "10", "discount.round_to": ""},
        )

        assert seen["discount"] == Discount(
            percent=Decimal("10"), round_to=Decimal("0.05"), only_pending=False
        )

    async def test_on_a_record_with_the_request(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await run(
            client, "add_note", {"keys": "3", "note": "Call first. ", "copies": "2"}
        )

        assert "Order 3 noted." in answer.text
        assert seen["order"].id == 3
        assert isinstance(seen["request"], Request)
        assert seen["order"].note == "Call first.Call first."

    async def test_left_empty_it_takes_the_default(
        self, client: httpx.AsyncClient
    ) -> None:
        await run(client, "add_note", {"keys": "3", "note": "Call", "copies": ""})

        assert seen["copies"] == 1

    async def test_a_picked_record_as_the_record(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await run(client, "hand_over", {"keys": "1", "customer": "2"})

        assert isinstance(seen["customer"], Customer)
        assert seen["customer"].id == 2
        assert f"handed to {seen['customer'].name}" in answer.text

    async def test_a_file_as_it_was_sent(self, client: httpx.AsyncClient) -> None:
        answer = await run(
            client,
            "read_file",
            {"kind": "returns"},
            files={"rows": ("rows.csv", b"a\nb\nc\n", "text/csv")},
        )

        assert "3 rows of returns read." in answer.text
        assert seen["filename"] == "rows.csv"
        assert isinstance(seen["session"], SessionAdapter)

    async def test_a_missing_value_stops_it(self, client: httpx.AsyncClient) -> None:
        answer = await run(client, "ship", {"keys": ["1"]})

        assert "Ship was not done. Carrier: This field is required." in answer.text
        assert seen == {}

    async def test_a_file_of_another_type_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await run(
            client, "read_file", {}, files={"rows": ("rows.txt", b"a", "text/plain")}
        )

        assert "Choose a file of this type: .csv." in answer.text
        assert seen == {}


class TestChoicesPerRequest:
    async def test_a_field_takes_the_place_of_the_typed_input(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/moves")
        answer = await client.post(
            "/admin/moves/action/move",
            data={"_csrf": token_in(page), "keys": ["1"], "warehouse": "north"},
            follow_redirects=True,
        )

        assert '<option value="north"' in dialog_for(page, "move")
        assert "Moved to north." in answer.text
        assert seen["warehouse"] == "north"

    async def test_a_value_never_offered_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/moves")
        answer = await client.post(
            "/admin/moves/action/move",
            data={"_csrf": token_in(page), "keys": ["1"], "warehouse": "west"},
            follow_redirects=True,
        )

        assert "Choose one of the listed options." in answer.text
        assert seen == {}


class TestSQLAlchemysOwnSession:
    async def test_it_is_handed_where_the_database_is_async(
        self, database: Database
    ) -> None:
        if not database.is_async:
            pytest.skip("only an async database has an AsyncSession")
        admin = Admin(database, views=[Counting], secret_key="for-the-session")
        async with serve(admin) as client:
            page = await client.get("/admin/counted")
            answer = await client.post(
                "/admin/counted/action/count",
                data={"_csrf": token_in(page)},
                follow_redirects=True,
            )

        assert re.search(r"Counted \d+ orders\.", answer.text)

    def test_a_sync_database_refuses_it_when_the_admin_starts(
        self, database: Database
    ) -> None:
        if database.is_async:
            pytest.skip("an async database has an AsyncSession")

        with pytest.raises(AdminSiteError) as raised:
            Admin(database, views=[Counting])

        assert str(raised.value) == (
            "Counting.count: session is an AsyncSession, and the admin's database "
            "is not async. Ask for a SessionAdapter, which works with both."
        )


@dataclass
class Address:
    city: str


@dataclass
class Shipping:
    address: Address


class TypedDict(ModelView[Order]):
    @action("Tag")
    async def tag(self, selection: Selection[Order], *, tags: dict[str, int]) -> str:
        return ""


class NamedLikeTheForm(ModelView[Order]):
    @action("Tag")
    async def tag(self, selection: Selection[Order], *, keys: str) -> str:
        return ""


class Untyped(ModelView[Order]):
    @action("Tag")
    async def tag(self, selection: Selection[Order], *, label) -> str:  # type: ignore[no-untyped-def]
        return ""


class OtherModelsSelection(ModelView[Order]):
    @action("Tag")
    async def tag(self, selection: Selection[Customer]) -> str:
        return ""


class RecordOnASelection(ModelView[Order]):
    @action("Tag")
    async def tag(self, order: Order) -> str:
        return ""


class SelectionOnARecord(ModelView[Order]):
    @action("Tag", on="record")
    async def tag(self, selection: Selection[Order]) -> str:
        return ""


class OptionItHasNoUseFor(ModelView[Order]):
    @action("Tag")
    async def tag(
        self,
        selection: Selection[Order],
        *,
        copies: Annotated[int, Input(multiline=True)],
    ) -> str:
        return ""


class GroupInAGroup(ModelView[Order]):
    @action("Ship")
    async def ship(self, selection: Selection[Order], *, shipping: Shipping) -> str:
        return ""


class InputWithNoParameter(ModelView[Order]):
    @action("Ship", inputs=[StringField("carrier")])
    async def ship(self, selection: Selection[Order]) -> str:
        return ""


class TypeNobodyImported(ModelView[Order]):
    @action("Ship")
    async def ship(
        self,
        selection: Selection[Order],
        *,
        carrier: "Nowhere",  # type: ignore[name-defined]  # noqa: F821
    ) -> str:
        return ""


REFUSED: dict[type[ModelView[Any]], list[str]] = {
    TypedDict: ["tags", "dict[str, int]", "cannot ask for"],
    NamedLikeTheForm: ["'keys'", "form already uses"],
    Untyped: ["give label a type"],
    OtherModelsSelection: ["Selection[Customer]", "Selection[Order]"],
    RecordOnASelection: ["order is typed Order", 'on="record"'],
    SelectionOnARecord: ["only an action on the selection", 'on="record"'],
    OptionItHasNoUseFor: ["copies", "Input(multiline=...)"],
    GroupInAGroup: ["shipping.address", "dataclass inside a dataclass"],
    InputWithNoParameter: ["'carrier'", "no parameter by that name"],
    TypeNobodyImported: ["Nowhere", "TYPE_CHECKING"],
}


class TestRefusedWhenTheAdminStarts:
    @pytest.mark.parametrize(
        "view", [pytest.param(view, id=view.__name__) for view in REFUSED]
    )
    def test_a_parameter_it_cannot_fill(self, view: type[ModelView[Any]]) -> None:
        with pytest.raises(AdminSiteError) as raised:
            view()

        message = str(raised.value)
        assert message.startswith(f"{view.__name__}.")
        for words in REFUSED[view]:
            assert words in message

    def test_the_message_lists_what_it_asks_for(self) -> None:
        with pytest.raises(AdminSiteError) as raised:
            TypedDict()

        assert str(raised.value) == (
            "TypedDict.tag: the parameter tags is typed dict[str, int], which an "
            "action cannot ask for. It asks for a str, int, float, Decimal, bool, "
            "date, datetime, time, UUID, Enum, Literal of strings, model, list of "
            "an Enum or of a model, UploadFile, or a dataclass of these, and hands "
            "over Request, AsyncSession and SessionAdapter."
        )
