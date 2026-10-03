from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import ForeignKey, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, Field, Inline, Link, ModelView, Statement
from adminsite.backends.sqlalchemy import Database
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import AdminSiteError, RecordNotFoundError
from adminsite.views.picker import PICKER_LIMIT
from tests.models import Order, OrderItem, Product
from tests.support import Backend, count_queries


class OrderView(ModelView[Order]):
    record_title = "Order {id}"
    fields = ["customer", "status", "note", "created_at"]
    inlines = (Inline("items", fields=("product", "quantity", "unit_price")),)


EDIT_FORM = {
    "customer": "1",
    "status": "SHIPPED",
    "note": "",
    "created_at": "2026-09-01T10:30",
}


def lines(*rows: dict[str, str]) -> dict[str, str]:
    """Form data for the order's lines, as the browser would send it."""
    data = {"items-count": str(len(rows))}
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"items-{index}-{key}"] = value
    return data


@pytest.fixture
def view() -> OrderView:
    return OrderView()


@pytest.fixture
def client(database: Database) -> httpx.AsyncClient:
    site = Admin(database, title="Shop")
    site.add_view(OrderView)
    app = Starlette()
    app.mount("/admin", site)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def client_for(database: Database, *views: type[ModelView[Any]]) -> httpx.AsyncClient:
    """A client for an admin showing these views."""
    app = Starlette()
    app.mount("/admin", Admin(database, title="Shop", views=list(views)))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def items_table(page: str) -> str:
    """The part of a record page from the Items table on."""
    return page.split(">Items<", 1)[1]


async def items_of(database: Database, order_id: int) -> list[OrderItem]:
    async with database.session() as session:
        found = await session.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.id)
        )
        return list(found.all())


class TestDeclaring:
    def test_an_inline_needs_a_relationship_holding_many(self) -> None:
        class Wrong(ModelView[Order]):
            name = "wrong"
            inlines = (Inline("customer"),)

        with pytest.raises(AdminSiteError, match="holds one record"):
            Wrong()

    def test_the_link_back_to_the_parent_is_not_an_input(self, view: OrderView) -> None:
        class Everything(ModelView[Order]):
            name = "everything"
            inlines = (Inline("items"),)

        fields = Everything()._inline_views["items"]._pages.form_fields()

        assert "order" not in fields
        assert "product" in fields

    def test_the_children_are_loaded_with_the_record(self, view: OrderView) -> None:
        paths = view._pages.load_paths()

        assert "items" in paths
        assert "items.product" in paths


class TestReadingTheForm:
    def test_each_row_comes_back_converted(self, view: OrderView) -> None:
        result = view._forms.parse(
            {
                **EDIT_FORM,
                **lines(
                    {"key": "1", "product": "2", "quantity": "3", "unit_price": "24.00"}
                ),
            }
        )

        [row] = result.inline_rows["items"]
        assert row.key == "1"
        assert row.values["quantity"] == 3
        assert row.values["unit_price"] == Decimal("24.00")

    def test_a_blank_row_is_skipped(self, view: OrderView) -> None:
        result = view._forms.parse(
            {
                **EDIT_FORM,
                **lines({"key": "", "product": "", "quantity": "", "unit_price": ""}),
            }
        )

        assert result.inline_rows["items"] == []
        assert result.ok

    def test_a_bad_value_is_reported_for_its_row(self, view: OrderView) -> None:
        result = view._forms.parse(
            {
                **EDIT_FORM,
                **lines(
                    {"key": "", "product": "1", "quantity": "lots", "unit_price": "1"}
                ),
            }
        )

        assert result.errors == {"items-0-quantity": "Enter a whole number."}

    def test_a_row_marked_for_removal_skips_its_values(self, view: OrderView) -> None:
        result = view._forms.parse(
            {
                **EDIT_FORM,
                **lines({"key": "1", "delete": "on", "quantity": "nonsense"}),
            }
        )

        [row] = result.inline_rows["items"]
        assert row.delete is True
        assert result.ok


class TestSaving:
    async def test_a_line_is_changed(self, database: Database, view: OrderView) -> None:
        async with database.session() as session:
            order = await view._reader.fetch_record(
                session, 1, paths=view._pages.load_paths()
            )
            assert order is not None
            first = order.items[0]
            result = view._forms.parse(
                {
                    **EDIT_FORM,
                    **lines(
                        {
                            "key": str(first.id),
                            "product": str(first.product_id),
                            "quantity": "9",
                            "unit_price": "59.00",
                        }
                    ),
                }
            )

            await view._saver.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        assert (await items_of(database, 1))[0].quantity == 9

    async def test_a_line_is_added(self, database: Database, view: OrderView) -> None:
        before = len(await items_of(database, 1))
        async with database.session() as session:
            order = await view._reader.fetch_record(
                session, 1, paths=view._pages.load_paths()
            )
            assert order is not None
            result = view._forms.parse(
                {
                    **EDIT_FORM,
                    **lines(
                        {
                            "key": "",
                            "product": "3",
                            "quantity": "2",
                            "unit_price": "38.50",
                        }
                    ),
                }
            )

            await view._saver.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        after = await items_of(database, 1)
        assert len(after) == before + 1
        assert after[-1].product_id == 3

    async def test_a_line_is_removed(self, database: Database, view: OrderView) -> None:
        existing = await items_of(database, 1)
        async with database.session() as session:
            order = await view._reader.fetch_record(
                session, 1, paths=view._pages.load_paths()
            )
            assert order is not None
            result = view._forms.parse(
                {**EDIT_FORM, **lines({"key": str(existing[0].id), "delete": "on"})}
            )

            await view._saver.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        remaining = await items_of(database, 1)
        assert len(remaining) == len(existing) - 1
        assert existing[0].id not in {item.id for item in remaining}

    async def test_a_new_order_comes_with_its_lines(
        self, database: Database, view: OrderView
    ) -> None:
        async with database.session() as session:
            result = view._forms.parse(
                {
                    **EDIT_FORM,
                    **lines(
                        {
                            "key": "",
                            "product": "1",
                            "quantity": "1",
                            "unit_price": "59.00",
                        },
                        {
                            "key": "",
                            "product": "2",
                            "quantity": "2",
                            "unit_price": "24.00",
                        },
                    ),
                }
            )

            order = await view._saver.save(
                session, result.values, inline_rows=result.inline_rows
            )
            key = order.id

        assert len(await items_of(database, key)) == 2

    async def test_a_line_from_another_order_is_refused(
        self, database: Database, view: OrderView
    ) -> None:
        someone_elses = (await items_of(database, 2))[0]
        async with database.session() as session:
            order = await view._reader.fetch_record(
                session, 1, paths=view._pages.load_paths()
            )
            assert order is not None
            result = view._forms.parse(
                {
                    **EDIT_FORM,
                    **lines(
                        {
                            "key": str(someone_elses.id),
                            "product": "1",
                            "quantity": "99",
                            "unit_price": "1.00",
                        }
                    ),
                }
            )

            with pytest.raises(RecordNotFoundError):
                await view._saver.save(
                    session,
                    result.values,
                    record=order,
                    inline_rows=result.inline_rows,
                )

        assert (await items_of(database, 2))[0].quantity == someone_elses.quantity


class TestPages:
    async def test_the_edit_page_shows_the_lines(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/1/edit")

        assert 'name="items-count"' in response.text
        assert 'name="items-0-quantity"' in response.text
        assert "Linen shirt" in response.text
        assert "Add a row" in response.text

    async def test_a_searched_line_names_its_record_as_the_form_does(
        self, database: Database
    ) -> None:
        # Over a hundred products, so a line's product is searched, not listed.
        async with database.session() as session:
            for number in range(PICKER_LIMIT):
                await session.add(
                    Product(name=f"Spare part {number}", price=Decimal("1.00"))
                )
            await session.commit()

        class ProductView(ModelView[Product]):
            record_title = "{name} at {price}"

        site = Admin(database, views=[OrderView, ProductView])
        app = Starlette()
        app.mount("/admin", site)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/admin/orders/1/edit")

        assert '"label": "Linen shirt at 59.00"' in response.text

    async def test_a_line_added_in_the_page_can_search(
        self, client: httpx.AsyncClient
    ) -> None:
        # A row pasted in from the blank one is new to htmx, which has to be
        # told of it, or the search in its picker never reaches the server.
        response = await client.get("/admin/orders/1/edit")

        assert "htmx.process($refs.body.lastElementChild)" in response.text

    async def test_the_new_page_offers_a_blank_line(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/new")

        assert 'name="items-0-product"' in response.text

    async def test_the_blank_line_does_not_make_the_form_required(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/new")
        table = response.text.split('name="items-count"')[1]

        assert "required" not in table.split("</section>")[0]

    async def test_lines_are_saved_through_the_form(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        existing = await items_of(database, 1)
        rows = [
            {
                "key": str(item.id),
                "product": str(item.product_id),
                "quantity": "7",
                "unit_price": str(item.unit_price),
            }
            for item in existing
        ]

        response = await client.post(
            "/admin/orders/1/edit", data={**EDIT_FORM, **lines(*rows)}
        )

        assert response.status_code == 303
        assert {item.quantity for item in await items_of(database, 1)} == {7}

    async def test_a_bad_line_keeps_what_was_typed(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            count_before = await session.scalar(
                select(func.count()).select_from(OrderItem)
            )

        response = await client.post(
            "/admin/orders/1/edit",
            data={
                **EDIT_FORM,
                **lines(
                    {"key": "", "product": "1", "quantity": "lots", "unit_price": "5"}
                ),
            },
        )

        assert response.status_code == 422
        assert "Enter a whole number." in response.text
        assert 'value="lots"' in response.text
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(OrderItem))
                == count_before
            )

    async def test_the_detail_page_lists_the_lines(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/1")

        assert "Items" in response.text
        assert "Linen shirt" in response.text
        assert "59.00" in response.text


class PricedOrderView(ModelView[Order]):
    """Orders whose lines keep the price they were sold at."""

    fields = ["customer", "status", "note", "created_at"]
    inlines = [
        Inline(
            Order.items,
            fields=[
                OrderItem.product,
                OrderItem.quantity,
                Field(OrderItem.unit_price, read_only=True),
            ],
        )
    ]


class TestAReadOnlyField:
    async def test_the_form_shows_the_price_without_an_input(
        self, database: Database
    ) -> None:
        site = Admin(database, title="Shop", views=[PricedOrderView])
        app = Starlette()
        app.mount("/admin", site)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/admin/orders/1/edit")

        assert response.status_code == 200
        assert 'name="items-0-quantity"' in response.text
        assert 'name="items-0-unit_price"' not in response.text
        assert "59.00" in response.text

    async def test_a_line_keeps_its_price_whatever_the_form_sends(
        self, database: Database
    ) -> None:
        view = PricedOrderView()
        async with database.session() as session:
            order = await view._reader.fetch_record(
                session, 1, paths=view._pages.load_paths()
            )
            assert order is not None
            first = order.items[0]
            price = first.unit_price
            result = view._forms.parse(
                {
                    **EDIT_FORM,
                    **lines(
                        {
                            "key": str(first.id),
                            "product": str(first.product_id),
                            "quantity": "9",
                            "unit_price": "0.01",
                        }
                    ),
                }
            )
            await view._saver.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        saved = (await items_of(database, 1))[0]
        assert saved.quantity == 9
        assert saved.unit_price == price


class TestCommitting:
    async def test_a_save_through_the_form_commits_once(
        self,
        client: httpx.AsyncClient,
        database: Database,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rows = [
            {
                "key": str(item.id),
                "product": str(item.product_id),
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
            }
            for item in await items_of(database, 1)
        ]
        commits: list[SessionAdapter] = []
        commit = SessionAdapter.commit

        async def counted(session: SessionAdapter) -> None:
            commits.append(session)
            await commit(session)

        monkeypatch.setattr(SessionAdapter, "commit", counted)

        response = await client.post(
            "/admin/orders/1/edit", data={**EDIT_FORM, **lines(*rows)}
        )

        assert response.status_code == 303
        assert len(commits) == 1


class OnceSetOrderView(ModelView[Order]):
    """Orders whose lines keep the quantity they were added with."""

    name = "orders"
    fields = ["customer", "status", "note", "created_at"]
    inlines = [
        Inline(
            Order.items,
            fields=[
                OrderItem.product,
                Field(OrderItem.quantity, exclude_from_edit=True),
                Field(OrderItem.unit_price, exclude_from_detail=True),
            ],
        )
    ]


class TestPageFlags:
    async def test_a_field_excluded_from_edit_is_set_once(
        self, database: Database
    ) -> None:
        existing = await items_of(database, 1)
        rows = [
            {
                "key": str(item.id),
                "product": str(item.product_id),
                "quantity": "99",
                "unit_price": str(item.unit_price),
            }
            for item in existing
        ]
        rows.append({"key": "", "product": "3", "quantity": "5", "unit_price": "38.50"})

        async with client_for(database, OnceSetOrderView) as client:
            form = await client.get("/admin/orders/1/edit")
            saved = await client.post(
                "/admin/orders/1/edit", data={**EDIT_FORM, **lines(*rows)}
            )

        assert 'name="items-0-quantity"' not in form.text
        assert 'name="items-0-unit_price"' in form.text
        assert 'name="items-__index__-quantity"' in form.text
        assert saved.status_code == 303
        quantities = [item.quantity for item in await items_of(database, 1)]
        assert quantities == [*(item.quantity for item in existing), 5]

    async def test_the_record_page_leaves_off_a_field_excluded_from_detail(
        self, database: Database
    ) -> None:
        async with client_for(database, OnceSetOrderView) as client:
            page = await client.get("/admin/orders/1")

        table = items_table(page.text)
        assert "Linen shirt" in table
        assert "Unit price" not in table
        assert "59.00" not in table

    async def test_a_field_excluded_from_create_shows_on_existing_lines(
        self, database: Database
    ) -> None:
        class ChosenOnceOrderView(ModelView[Order]):
            name = "orders"
            fields = ["customer", "status", "note", "created_at"]
            inlines = [
                Inline(
                    Order.items,
                    fields=[
                        Field(OrderItem.product, exclude_from_create=True),
                        OrderItem.quantity,
                    ],
                )
            ]

        async with client_for(database, ChosenOnceOrderView) as client:
            form = await client.get("/admin/orders/1/edit")
            page = await client.get("/admin/orders/1")

        assert 'name="items-0-product"' in form.text
        assert 'name="items-__index__-product"' not in form.text
        assert 'name="items-__index__-quantity"' in form.text
        assert "Linen shirt" in items_table(page.text)


class TestALinkInTheFields:
    async def test_the_record_page_shows_it_without_another_query(
        self, backend: Backend
    ) -> None:
        class LinkedOrderView(ModelView[Order]):
            name = "orders"
            fields = ["customer", "status", "note", "created_at"]
            inlines = [
                Inline(
                    Order.items,
                    fields=[Link(OrderItem.product, Product.name), OrderItem.quantity],
                )
            ]

        class PlainOrderView(LinkedOrderView):
            name = "plain_orders"
            inlines = [
                Inline(Order.items, fields=[OrderItem.product, OrderItem.quantity])
            ]

        async with client_for(
            backend.database, LinkedOrderView, PlainOrderView
        ) as client:
            with count_queries(backend) as linked:
                page = await client.get("/admin/orders/1")
            with count_queries(backend) as plain:
                await client.get("/admin/plain_orders/1")

        assert page.status_code == 200
        table = items_table(page.text)
        assert "Product name" in table
        assert "Linen shirt" in table
        assert linked.count == plain.count

    async def test_the_form_leaves_it_off(self, database: Database) -> None:
        class LinkedOrderView(ModelView[Order]):
            name = "orders"
            fields = ["customer", "status", "note", "created_at"]
            inlines = [
                Inline(
                    Order.items,
                    fields=[
                        OrderItem.product,
                        Link(OrderItem.product, Product.price),
                        OrderItem.quantity,
                    ],
                )
            ]

        async with client_for(database, LinkedOrderView) as client:
            form = await client.get("/admin/orders/1/edit")

        assert form.status_code == 200
        assert 'name="items-0-quantity"' in form.text
        assert "product.price" not in form.text


class ScopedProductView(ModelView[Product]):
    """Products without the first, which nobody here may pick."""

    name = "products"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Product.id != 1)


class TestARefusedLink:
    async def test_the_message_is_shown_in_the_row_s_cell(
        self, database: Database
    ) -> None:
        rows = [
            {
                "key": str(item.id),
                "product": "2",
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
            }
            for item in await items_of(database, 1)
        ]
        rows.append({"key": "", "product": "1", "quantity": "1", "unit_price": "5"})

        async with client_for(database, OrderView, ScopedProductView) as client:
            response = await client.post(
                "/admin/orders/1/edit", data={**EDIT_FORM, **lines(*rows)}
            )

        assert response.status_code == 422
        assert "Some fields need another look." in response.text
        assert 'href="#field-items-2-product">Items, row 3, product</a>' in (
            response.text
        )
        assert 'aria-invalid="true"' in response.text
        assert "#field-customer" not in response.text


class DefaultedOrderView(ModelView[Order]):
    """Orders whose lines take their quantity from the model's default."""

    name = "orders"
    fields = ["customer", "status", "note", "created_at"]
    inlines = [
        Inline(
            Order.items,
            fields=[
                OrderItem.product,
                Field(OrderItem.quantity, read_only=True),
                OrderItem.unit_price,
            ],
        )
    ]


class TestAReadOnlyFieldWithADefault:
    async def test_a_new_line_takes_the_default(self, database: Database) -> None:
        rows = [
            {
                "key": str(item.id),
                "product": str(item.product_id),
                "unit_price": str(item.unit_price),
            }
            for item in await items_of(database, 1)
        ]
        rows.append({"key": "", "product": "3", "quantity": "7", "unit_price": "38.50"})

        async with client_for(database, DefaultedOrderView) as client:
            response = await client.post(
                "/admin/orders/1/edit", data={**EDIT_FORM, **lines(*rows)}
            )

        assert response.status_code == 303
        assert (await items_of(database, 1))[-1].quantity == 1


class LinesOnceSavedOrderView(ModelView[Order]):
    fields = [Order.customer, Order.status, Order.note, Order.created_at]
    inlines = [Inline(Order.items, fields=[OrderItem.product, OrderItem.quantity])]

    def get_inlines(self, request: Request, record: Order | None) -> list[Inline]:
        # A new order takes its lines once it is saved.
        return list(self.inlines) if record is not None else []


class TestInlinesPerRecord:
    async def test_lines_shown_only_once_saved_are_loaded_with_the_record(
        self, database: Database
    ) -> None:
        async with client_for(database, LinesOnceSavedOrderView) as client:
            edit = await client.get("/admin/orders/1/edit")
            detail = await client.get("/admin/orders/1")
            new = await client.get("/admin/orders/new")

        assert edit.status_code == detail.status_code == new.status_code == 200
        assert 'name="items-0-quantity"' in edit.text
        assert "Linen shirt" in items_table(detail.text)
        assert "items-count" not in new.text


class LinkBase(DeclarativeBase):
    """Kept apart from the test models, so only these tests make the tables."""


class Team(LinkBase):
    __tablename__ = "inline_teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))

    home_matches: Mapped[list["Match"]] = relationship(
        back_populates="home_team",
        foreign_keys="Match.home_team_id",
        cascade="all, delete-orphan",
    )
    # One way: a player has no link back to the team.
    players: Mapped[list["Player"]] = relationship(cascade="all, delete-orphan")

    def __str__(self) -> str:
        return self.name


class Match(LinkBase):
    """Played between two teams, so it links to a team more than once."""

    __tablename__ = "inline_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("inline_teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("inline_teams.id"))
    week: Mapped[int]

    home_team: Mapped[Team] = relationship(
        back_populates="home_matches", foreign_keys=[home_team_id]
    )
    # The home team again, read only, as a report might name it.
    host: Mapped[Team] = relationship(foreign_keys=[home_team_id], viewonly=True)
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class Player(LinkBase):
    __tablename__ = "inline_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("inline_teams.id"))
    name: Mapped[str] = mapped_column(String(60))


class TeamView(ModelView[Team]):
    fields = [Team.name]
    inlines = [
        Inline(Team.home_matches, fields=[Match.away_team, Match.week]),
        Inline(Team.players),
    ]


@pytest.fixture
async def teams(backend: Backend) -> AsyncIterator[Database]:
    """The test database with three teams."""
    database = backend.database
    async with database.session() as session:
        await session.run(
            lambda plain: LinkBase.metadata.create_all(plain.connection())
        )
        for name in ("Reds", "Blues", "Greens"):
            await session.add(Team(name=name))
        await session.commit()
    yield database
    async with database.session() as session:
        await session.run(lambda plain: LinkBase.metadata.drop_all(plain.connection()))
        await session.commit()


class TestWhatTheParentFills:
    def test_with_no_fields_every_link_over_its_column_is_left_out(self) -> None:
        class EveryMatchTeamView(ModelView[Team]):
            inlines = [Inline(Team.home_matches)]

        child = EveryMatchTeamView()._inline_views["home_matches"]

        assert child._pages.form_fields() == ("away_team", "week")
        assert child._pages.detail_fields() == ("id", "away_team", "week")

    def test_a_link_over_its_column_named_in_the_fields_is_left_out(self) -> None:
        class HostTeamView(ModelView[Team]):
            inlines = [
                Inline(
                    Team.home_matches,
                    fields=[Match.host, Match.home_team_id, Match.week],
                )
            ]

        child = HostTeamView()._inline_views["home_matches"]

        assert child._pages.form_fields() == ("week",)
        assert child._pages.detail_fields() == ("week",)

    def test_another_link_to_the_parent_stays(self) -> None:
        child = TeamView()._inline_views["home_matches"]

        assert child._pages.form_fields() == ("away_team", "week")
        assert child._pages.form_fields(record=Match(id=1)) == ("away_team", "week")
        assert child._pages.detail_fields() == ("away_team", "week")

    def test_with_no_link_back_the_column_is_left_out(self) -> None:
        child = TeamView()._inline_views["players"]

        assert child._pages.form_fields() == ("name",)
        assert child._pages.detail_fields() == ("id", "name")

    async def test_rows_save_with_another_link_to_the_parent(
        self, teams: Database
    ) -> None:
        form = {
            "name": "Reds",
            "home_matches-count": "1",
            "home_matches-0-key": "",
            "home_matches-0-away_team": "2",
            "home_matches-0-week": "1",
            "players-count": "1",
            "players-0-key": "",
            "players-0-name": "Ana",
        }

        async with client_for(teams, TeamView) as client:
            created = await client.post("/admin/teams/1/edit", data=form)
            changed = await client.post(
                "/admin/teams/1/edit",
                data={
                    "name": "Reds",
                    "home_matches-count": "1",
                    "home_matches-0-key": "1",
                    "home_matches-0-away_team": "3",
                    "home_matches-0-week": "2",
                },
            )
            page = await client.get("/admin/teams/1")

        assert created.status_code == changed.status_code == 303
        assert "Greens" in page.text
        async with teams.session() as session:
            matches = (await session.scalars(select(Match))).all()
            players = (await session.scalars(select(Player))).all()
        assert [(m.home_team_id, m.away_team_id, m.week) for m in matches] == [
            (1, 3, 2)
        ]
        assert [(player.team_id, player.name) for player in players] == [(1, "Ana")]
