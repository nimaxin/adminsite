from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from starlette.applications import Starlette

from adminsite import Admin, Inline, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError, RecordNotFoundError
from tests.models import Order, OrderItem


class OrderView(ModelView, model=Order):
    display_template = "Order {id}"
    form_fields = ("customer", "status", "note", "created_at")
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
        class Wrong(ModelView, model=Order):
            name = "wrong"
            inlines = (Inline("customer"),)

        with pytest.raises(AdminSiteError, match="holds one record"):
            Wrong()

    def test_the_link_back_to_the_parent_is_not_an_input(self, view: OrderView) -> None:
        class Everything(ModelView, model=Order):
            name = "everything"
            inlines = (Inline("items"),)

        fields = Everything().inline_view("items").get_form_fields()

        assert "order" not in fields
        assert "product" in fields

    def test_the_children_are_loaded_with_the_record(self, view: OrderView) -> None:
        paths = view.get_load_paths()

        assert "items" in paths
        assert "items.product" in paths


class TestReadingTheForm:
    def test_each_row_comes_back_converted(self, view: OrderView) -> None:
        result = view.parse_form(
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
        result = view.parse_form(
            {
                **EDIT_FORM,
                **lines({"key": "", "product": "", "quantity": "", "unit_price": ""}),
            }
        )

        assert result.inline_rows["items"] == []
        assert result.ok

    def test_a_bad_value_is_reported_for_its_row(self, view: OrderView) -> None:
        result = view.parse_form(
            {
                **EDIT_FORM,
                **lines(
                    {"key": "", "product": "1", "quantity": "lots", "unit_price": "1"}
                ),
            }
        )

        assert result.errors == {"items-0-quantity": "Enter a whole number."}

    def test_a_row_marked_for_removal_skips_its_values(self, view: OrderView) -> None:
        result = view.parse_form(
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
            order = await view.fetch_record(session, 1, paths=view.get_load_paths())
            assert order is not None
            first = order.items[0]
            result = view.parse_form(
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

            await view.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        assert (await items_of(database, 1))[0].quantity == 9

    async def test_a_line_is_added(self, database: Database, view: OrderView) -> None:
        before = len(await items_of(database, 1))
        async with database.session() as session:
            order = await view.fetch_record(session, 1, paths=view.get_load_paths())
            assert order is not None
            result = view.parse_form(
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

            await view.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        after = await items_of(database, 1)
        assert len(after) == before + 1
        assert after[-1].product_id == 3

    async def test_a_line_is_removed(self, database: Database, view: OrderView) -> None:
        existing = await items_of(database, 1)
        async with database.session() as session:
            order = await view.fetch_record(session, 1, paths=view.get_load_paths())
            assert order is not None
            result = view.parse_form(
                {**EDIT_FORM, **lines({"key": str(existing[0].id), "delete": "on"})}
            )

            await view.save(
                session, result.values, record=order, inline_rows=result.inline_rows
            )

        remaining = await items_of(database, 1)
        assert len(remaining) == len(existing) - 1
        assert existing[0].id not in {item.id for item in remaining}

    async def test_a_new_order_comes_with_its_lines(
        self, database: Database, view: OrderView
    ) -> None:
        async with database.session() as session:
            result = view.parse_form(
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

            order = await view.save(
                session, result.values, inline_rows=result.inline_rows
            )
            key = order.id

        assert len(await items_of(database, key)) == 2

    async def test_a_line_from_another_order_is_refused(
        self, database: Database, view: OrderView
    ) -> None:
        someone_elses = (await items_of(database, 2))[0]
        async with database.session() as session:
            order = await view.fetch_record(session, 1, paths=view.get_load_paths())
            assert order is not None
            result = view.parse_form(
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
                await view.save(
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
        assert "Add another" in response.text

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
