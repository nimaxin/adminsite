import httpx
import pytest
from sqlalchemy import func, select
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import RefusedError
from adminsite.views.writing import SaveContext
from tests.models import Customer, Order, Product
from tests.support import spare_product


class ProductView(ModelView, model=Product):
    list_display = ("id", "name", "price")
    form_fields = ("name", "price", "description")


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total")
    form_fields = ("customer", "status", "total", "note", "created_at")
    readonly_fields = ("total",)
    display_template = "Order {id}"


class CustomerView(ModelView, model=Customer):
    display_template = "{name} ({email})"
    form_fields = ("name", "email", "region", "is_active")


class GuardedProducts(ModelView, model=Product):
    name = "guarded"
    form_fields = ("name", "price")

    async def before_save(self, context: SaveContext) -> None:
        if context.values.get("name") == "Forbidden":
            raise RefusedError("That name is taken.")


@pytest.fixture
def admin(database: Database) -> Admin:
    site = Admin(database, title="Shop")
    site.add_view(ProductView)
    site.add_view(OrderView)
    site.add_view(CustomerView)
    return site


@pytest.fixture
def client(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestDetail:
    async def test_it_shows_one_record(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/products/1")

        assert response.status_code == 200
        assert "Linen shirt" in response.text

    async def test_the_heading_uses_the_display_template(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/1")

        assert "Order 1" in response.text

    async def test_a_missing_record_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products/9999")

        assert response.status_code == 404

    async def test_an_empty_value_shows_a_dash(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/products/1")

        assert "—" in response.text


class TestCreating:
    async def test_the_form_has_a_field_for_each_column(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products/new")

        assert response.status_code == 200
        assert 'name="name"' in response.text
        assert 'name="price"' in response.text
        assert 'name="description"' in response.text

    async def test_a_record_is_created_and_shown(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/admin/products/new",
            data={"name": "Felt hat", "price": "42.00", "description": ""},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "Felt hat" in response.text

    async def test_it_lands_on_the_new_record(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/admin/products/new",
            data={"name": "Wool cap", "price": "15.00"},
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/admin/products/")

    async def test_a_bad_value_comes_back_on_the_form(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/admin/products/new", data={"name": "Hat", "price": "free"}
        )

        assert response.status_code == 422
        assert "Enter an amount" in response.text
        assert 'value="Hat"' in response.text

    async def test_nothing_is_saved_when_the_form_is_wrong(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post(
            "/admin/products/new", data={"name": "Ghost", "price": "free"}
        )

        async with database.session() as session:
            found = await session.scalar(
                select(func.count()).select_from(Product).where(Product.name == "Ghost")
            )
            assert found == 0

    async def test_a_hook_refusing_shows_the_reason(
        self, admin: Admin, client: httpx.AsyncClient
    ) -> None:
        admin.add_view(GuardedProducts)

        response = await client.post(
            "/admin/guarded/new", data={"name": "Forbidden", "price": "1.00"}
        )

        assert response.status_code == 422
        assert "That name is taken." in response.text


class TestEditing:
    async def test_the_form_is_filled_with_the_record(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products/1/edit")

        assert 'value="Linen shirt"' in response.text

    async def test_a_change_is_saved(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt v2", "price": "61.00", "description": ""},
        )

        assert response.status_code == 303
        async with database.session() as session:
            record = await session.get(Product, 1)
            assert record is not None
            assert record.name == "Linen shirt v2"

    async def test_a_readonly_field_cannot_be_changed(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            before = await session.get(Order, 1)
            assert before is not None
            total = before.total

        await client.post(
            "/admin/orders/1/edit",
            data={
                "customer": "1",
                "status": "PAID",
                "total": "0.01",
                "note": "",
                "created_at": "2026-09-18T10:00",
            },
        )

        async with database.session() as session:
            after = await session.get(Order, 1)
            assert after is not None
            assert after.total == total

    async def test_a_link_can_be_changed_by_picking_another_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post(
            "/admin/orders/1/edit",
            data={
                "customer": "2",
                "status": "PAID",
                "note": "",
                "created_at": "2026-09-18T10:00",
            },
        )

        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.customer_id == 2

    async def test_the_form_offers_the_records_to_link_to(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/1/edit")

        assert "Lena Fischer (lena@fischer.de)" in response.text


class TestDeleting:
    async def test_a_record_is_deleted(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        key = await spare_product(database)

        response = await client.post(f"/admin/products/{key}/delete")

        assert response.status_code == 303
        async with database.session() as session:
            assert await session.get(Product, key) is None

    async def test_a_record_still_in_use_is_kept_and_the_user_told(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post("/admin/products/1/delete")

        assert response.status_code == 303
        async with database.session() as session:
            assert await session.get(Product, 1) is not None

    async def test_a_key_that_is_not_a_number_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products/not-a-number")

        assert response.status_code == 404

    async def test_the_edit_page_asks_in_a_dialog_before_deleting(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products/1/edit")

        assert 'id="confirm-delete"' in response.text
        assert 'action="/admin/products/1/delete"' in response.text
        assert "confirm(" not in response.text

    async def test_deleting_something_gone_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/admin/products/9999/delete")

        assert response.status_code == 404


class TestLookup:
    async def test_it_lists_records_to_pick_from(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/lookup/customer")

        assert response.status_code == 200
        assert "Lena Fischer" in response.text

    async def test_it_narrows_by_what_was_typed(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/lookup/customer?q=rossi")

        assert "Marco Rossi" in response.text
        assert "Lena Fischer" not in response.text

    async def test_nothing_found_says_so(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders/lookup/customer?q=zzzz")

        assert "Nothing found." in response.text

    async def test_a_field_that_is_not_a_link_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders/lookup/status")

        assert response.status_code == 404
