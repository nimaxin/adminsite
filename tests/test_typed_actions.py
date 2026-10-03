"""An action asks for its values with typed parameters, and is handed the rest."""

import dataclasses
import enum
import re
from collections.abc import AsyncIterator
from dataclasses import InitVar, dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, Literal, assert_type
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.requests import Request

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Action, Input, Selection, action
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.exceptions import AdminSiteError
from adminsite.fields import (
    BooleanField,
    DateField,
    DateTimeField,
    DecimalField,
    EnumField,
    FloatField,
    IntegerField,
    RelationField,
    StringField,
    TextAreaField,
    TimeField,
    UUIDField,
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


@dataclass
class Signed:
    """A note, signed with the initials of whoever wrote it."""

    note: str
    initials: InitVar[str]

    def __post_init__(self, initials: str) -> None:
        self.note = f"{self.note} ({initials})"


@dataclass(frozen=True)
class Markdown:
    percent: Decimal


@dataclass(frozen=True)
class Refund:
    percent: Decimal


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

    @action("Stamp", on="record")
    async def stamp(self, *, order: Order) -> str:
        seen["stamped"] = order
        return f"Order {order.id} stamped."

    @action("Sign")
    async def sign(self, selection: Selection[Order], *, signed: Signed) -> str:
        seen["signed"] = signed
        return "Signed."

    @action("Adjust")
    async def adjust(
        self, selection: Selection[Order], *, markdown: Markdown, refund: Refund
    ) -> str:
        return "Adjusted."

    @action("Print labels")
    async def print_labels(
        self, selection: Selection[Order], *, copies: int | None = 2
    ) -> str:
        seen["copies"] = copies
        return "Printed."

    @action("Look")
    async def look(
        self,
        selection: Selection[Order],
        request: Request | None = None,
        session: SessionAdapter | None = None,
    ) -> str:
        seen.update(request=request, session=session)
        return "Looked."

    @action("Schedule")
    async def schedule(
        self,
        selection: Selection[Order],
        *,
        rate: float,
        at: datetime,
        opens: time,
        code: UUID,
        carriers: list[Carrier],
    ) -> str:
        seen.update(rate=rate, at=at, opens=opens, code=code, carriers=carriers)
        return "Scheduled."

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
    async def move(
        self, selection: Selection[Order], *, warehouse: str, note: str
    ) -> str:
        seen.update(warehouse=warehouse, note=note)
        return f"Moved to {warehouse}."

    async def by_hand(self, selection: Selection[Order], *, reason: str) -> str:
        seen["reason"] = reason
        return f"Done by hand: {reason}."

    def get_actions(self, request: Any = None) -> tuple[Any, ...]:
        choices = (("north", "North"), ("south", "South"))
        return (
            *(
                dataclasses.replace(
                    item, inputs=(EnumField("warehouse", choices=choices),)
                )
                if item.name == "move"
                else item
                for item in super().get_actions(request)
            ),
            Action(name="by_hand", label="By hand", method="by_hand"),
        )


class Twice(OrderView):
    """Offers two of its actions again, under other names."""

    name = "twice"

    def get_actions(self, request: Any = None) -> tuple[Any, ...]:
        found = super().get_actions(request)
        return (
            *found,
            *(
                dataclasses.replace(item, name=f"{item.name}_again", label="Again")
                for item in found
                if item.name in ("discount", "add_note")
            ),
        )


class Mistaken(ModelView[Order]):
    """Builds an action by hand that asks for a value its method never takes."""

    name = "mistaken"

    async def by_hand(self, selection: Selection[Order]) -> str:
        return "Done by hand."

    def get_actions(self, request: Any = None) -> tuple[Any, ...]:
        return (
            *super().get_actions(request),
            Action(
                name="by_hand",
                label="By hand",
                method="by_hand",
                inputs=(StringField("reason"),),
            ),
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
    return {item.name: item for item in OrderView()._actions.named(name).inputs}


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

    def test_a_literal_keeps_the_capitals_it_was_written_with(self) -> None:
        class Shipping(ModelView[Order]):
            @action("Ship")
            async def ship(
                self,
                selection: Selection[Order],
                *,
                carrier: Literal["DHL", "PostNL", "next_day"],
            ) -> str:
                return ""

        (carrier,) = Shipping()._actions.named("ship").inputs

        assert isinstance(carrier, EnumField)
        assert carrier.choices == (
            ("DHL", "DHL"),
            ("PostNL", "PostNL"),
            ("next_day", "Next day"),
        )

    def test_every_plain_type_has_its_input(self) -> None:
        asked = inputs_of("schedule")

        assert isinstance(asked["rate"], FloatField)
        assert isinstance(asked["at"], DateTimeField)
        assert isinstance(asked["opens"], TimeField)
        assert isinstance(asked["code"], UUIDField)
        assert isinstance(asked["carriers"], EnumField)
        assert asked["carriers"].enum is Carrier
        assert asked["carriers"].multiple is True

    def test_an_init_var_is_asked_for_with_the_fields(self) -> None:
        assert list(inputs_of("sign")) == ["signed.note", "signed.initials"]

    def test_the_record_goes_to_its_type_after_the_star(self) -> None:
        stamp = OrderView()._actions.named("stamp")

        assert stamp.inputs == ()
        assert stamp.needs_dialog is False

    def test_what_is_handed_may_be_typed_optional(self) -> None:
        look = OrderView()._actions.named("look")

        assert look.inputs == ()
        assert look.call is not None
        assert [(one.name, one.kind) for one in look.call.handed] == [
            ("selection", "subject"),
            ("request", "request"),
            ("session", "session"),
        ]

    def test_a_default_where_none_is_allowed_opens_the_dialog(self) -> None:
        copies = inputs_of("print_labels")["copies"]

        assert isinstance(copies, IntegerField)
        assert copies.default == 2
        assert copies.required is False

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

        asked = Notes()._actions.named("note").inputs

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

        carriers, customers = Offers()._actions.named("offer").inputs

        assert isinstance(carriers, EnumField)
        assert carriers.multiple is True
        assert isinstance(customers, RelationField)
        assert customers.collection is True
        assert customers.related_model is Customer

    def test_a_list_without_a_default_needs_a_value(self) -> None:
        class Offers(ModelView[Order]):
            @action("Offer")
            async def offer(
                self, selection: Selection[Order], *, customers: list[Customer]
            ) -> str:
                return ""

        view = Offers()
        read = view._forms.parse_action_inputs(view._actions.named("offer"), {})

        assert read.errors == {"customers": "This field is required."}


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

    async def test_emptied_where_none_is_allowed_it_is_none(
        self, client: httpx.AsyncClient
    ) -> None:
        await run(client, "print_labels", {"keys": ["1"], "copies": ""})

        assert seen["copies"] is None

    async def test_every_plain_type_as_its_type(
        self, client: httpx.AsyncClient
    ) -> None:
        code = UUID("12345678-1234-5678-1234-567812345678")
        answer = await run(
            client,
            "schedule",
            {
                "keys": ["1"],
                "rate": "0.5",
                "at": "2026-09-30T10:00",
                "opens": "09:30",
                "code": str(code),
                "carriers": ["DHL", "UPS"],
            },
        )

        assert "Scheduled." in answer.text
        assert seen["rate"] == 0.5
        assert isinstance(seen["rate"], float)
        assert seen["at"] == datetime(2026, 9, 30, 10, 0)
        assert seen["opens"] == time(9, 30)
        assert seen["code"] == code
        assert seen["carriers"] == [Carrier.DHL, Carrier.UPS]

    async def test_a_group_with_an_init_var(self, client: httpx.AsyncClient) -> None:
        await run(
            client,
            "sign",
            {"keys": ["1"], "signed.note": "Checked", "signed.initials": "NN"},
        )

        assert isinstance(seen["signed"], Signed)
        assert seen["signed"].note == "Checked (NN)"

    async def test_the_record_after_the_star(self, client: httpx.AsyncClient) -> None:
        answer = await run(client, "stamp", {"keys": "2"})

        assert "Order 2 stamped." in answer.text
        assert isinstance(seen["stamped"], Order)
        assert seen["stamped"].id == 2

    async def test_the_request_and_the_session_typed_optional(
        self, client: httpx.AsyncClient
    ) -> None:
        await run(client, "look", {"keys": ["1"]})

        assert isinstance(seen["request"], Request)
        assert isinstance(seen["session"], SessionAdapter)

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

    async def test_a_problem_in_a_group_names_the_group(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await run(
            client,
            "adjust",
            {"keys": ["1"], "markdown.percent": "a lot", "refund.percent": "some"},
        )

        wrong = "Enter an amount, for example 12.50."
        assert (
            f"Adjust was not done. Markdown, Percent: {wrong}; Refund, Percent: {wrong}"
            in answer.text
        )

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
            data={
                "_csrf": token_in(page),
                "keys": ["1"],
                "warehouse": "north",
                "note": "Fragile",
            },
            follow_redirects=True,
        )

        dialog = dialog_for(page, "move")
        assert '<option value="north"' in dialog
        assert 'name="note"' in dialog
        assert "Moved to north." in answer.text
        assert seen == {"warehouse": "north", "note": "Fragile"}

    async def test_an_action_built_by_hand_asks_for_its_values(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/moves")
        answer = await client.post(
            "/admin/moves/action/by_hand",
            data={"_csrf": token_in(page), "keys": ["1"], "reason": "Stock count"},
            follow_redirects=True,
        )

        assert 'name="reason"' in dialog_for(page, "by_hand")
        assert "Done by hand: Stock count." in answer.text
        assert seen["reason"] == "Stock count"

    async def test_a_value_never_offered_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/moves")
        answer = await client.post(
            "/admin/moves/action/move",
            data={
                "_csrf": token_in(page),
                "keys": ["1"],
                "warehouse": "west",
                "note": "Fragile",
            },
            follow_redirects=True,
        )

        assert "Choose one of the listed options." in answer.text
        assert seen == {}


class TestOfferedAgainUnderAnotherName:
    @pytest.fixture
    async def twice(self, database: Database) -> AsyncIterator[httpx.AsyncClient]:
        seen.clear()
        admin = Admin(
            database, views=[Twice, CustomerView], secret_key="for-the-session"
        )
        async with serve(admin) as client:
            yield client

    async def test_it_asks_for_what_the_action_asks_for(
        self, twice: httpx.AsyncClient
    ) -> None:
        page = await twice.get("/admin/twice")

        assert page.status_code == 200
        assert 'name="discount.percent"' in dialog_for(page, "discount_again")

    async def test_its_group_arrives_as_the_dataclass(
        self, twice: httpx.AsyncClient
    ) -> None:
        page = await twice.get("/admin/twice")
        answer = await twice.post(
            "/admin/twice/action/discount_again",
            data={"_csrf": token_in(page), "keys": ["1"], "discount.percent": "10"},
            follow_redirects=True,
        )

        assert "Discounted." in answer.text
        assert seen["discount"] == Discount(
            percent=Decimal("10"), round_to=Decimal("0.05"), only_pending=False
        )

    async def test_left_empty_it_takes_the_default(
        self, twice: httpx.AsyncClient
    ) -> None:
        page = await twice.get("/admin/twice")
        await twice.post(
            "/admin/twice/action/add_note_again",
            data={"_csrf": token_in(page), "keys": "3", "note": "Call", "copies": ""},
            follow_redirects=True,
        )

        assert seen["copies"] == 1


MISTAKEN_RUNS = [
    pytest.param(
        "POST", "/admin/mistaken/action/by_hand", {"data": {"keys": "1"}}, id="form"
    ),
    pytest.param(
        "POST",
        "/admin/-/api/mistaken/actions/by_hand",
        {"json": {"keys": ["1"]}},
        id="api",
    ),
    pytest.param(
        "GET", "/admin/mistaken/action/by_hand/lookup/reason", {}, id="lookup"
    ),
]


class TestAnActionThatIsNotThere:
    async def test_a_name_nothing_offers_is_not_found(self, database: Database) -> None:
        admin = Admin(database, views=[Mistaken], api=True)
        async with serve(admin) as client:
            by_form = await client.post(
                "/admin/mistaken/action/fly", data={"keys": "1"}
            )
            by_api = await client.post(
                "/admin/-/api/mistaken/actions/fly", json={"keys": ["1"]}
            )
            lookup = await client.get("/admin/mistaken/action/fly/lookup/reason")

        assert [answer.status_code for answer in (by_form, by_api, lookup)] == [
            404,
            404,
            404,
        ]
        assert by_api.json() == {"error": "No such action."}

    @pytest.mark.parametrize(("method", "url", "sent"), MISTAKEN_RUNS)
    async def test_a_mistake_in_one_is_not_taken_for_a_missing_one(
        self, database: Database, method: str, url: str, sent: dict[str, Any]
    ) -> None:
        admin = Admin(database, views=[Mistaken], api=True)
        async with serve(admin) as client:
            with pytest.raises(AdminSiteError) as raised:
                await client.request(method, url, **sent)

        assert str(raised.value) == (
            "Mistaken.by_hand: inputs asks for 'reason', and the method has no "
            "parameter by that name. Add reason to its parameters."
        )


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


class StarArgs(ModelView[Order]):
    @action("Tag")
    async def tag(self, selection: Selection[Order], *labels: str) -> str:
        return ""


class PositionalOnly(ModelView[Order]):
    @action("Tag")
    async def tag(self, selection: Selection[Order], label: str, /) -> str:
        return ""


class OptionalGroup(ModelView[Order]):
    @action("Ship")
    async def ship(
        self, selection: Selection[Order], *, address: Address | None = None
    ) -> str:
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
    StarArgs: ["*labels cannot be filled in", "Name each parameter"],
    PositionalOnly: ["label comes before a /", "Move the / before it"],
    OptionalGroup: ["address is typed Address | None", "always asked for"],
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
