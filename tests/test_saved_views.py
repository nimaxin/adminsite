import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView, SavedView, SavedViews
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from adminsite.saved_views import clean_query
from tests.models import Order


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    list_columns = ("note", "created_at")
    search_fields = ("customer.name",)
    list_filter = ("status",)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SavedViews]:
    views = SavedViews(f"sqlite:///{tmp_path / 'views.db'}")
    yield views
    views.close()


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


def headers(page: httpx.Response) -> list[str]:
    head = page.text.split("<thead>", 1)[1].split("</thead>", 1)[0]
    head = re.sub(r'<span class="sr-only">.*?</span>', "", head)
    return re.findall(r">\s*([A-Z][a-z ]+?)\s*(?:<|&)", head)


class TestTheStore:
    async def test_a_view_is_kept_for_its_owner(self, store: SavedViews) -> None:
        await store.save(SavedView("orders", "Paid", "status=PAID", owner="nima"))

        mine = await store.visible_to("orders", "nima")
        theirs = await store.visible_to("orders", "sara")

        assert [item.name for item in mine] == ["Paid"]
        assert theirs == []

    async def test_a_shared_view_is_seen_by_everyone(self, store: SavedViews) -> None:
        await store.save(
            SavedView("orders", "Paid", "status=PAID", owner="nima", shared=True)
        )

        assert [item.name for item in await store.visible_to("orders", "sara")] == [
            "Paid"
        ]

    async def test_views_belong_to_one_list(self, store: SavedViews) -> None:
        await store.save(SavedView("orders", "Paid", "status=PAID"))

        assert await store.visible_to("customers", None) == []

    async def test_the_page_is_not_saved(self, store: SavedViews) -> None:
        saved = await store.save(
            SavedView("orders", "Paid", "status=PAID&page=4&after=abc&q=lena")
        )

        assert saved.query == "status=PAID&q=lena"
        assert saved.id is not None

    async def test_only_the_owner_removes_a_view(self, store: SavedViews) -> None:
        saved = await store.save(
            SavedView("orders", "Paid", "status=PAID", owner="nima", shared=True)
        )
        assert saved.id is not None

        assert await store.delete(saved.id, "sara") is False
        assert await store.delete(saved.id, "nima") is True
        assert await store.visible_to("orders", "nima") == []

    async def test_your_own_database_is_left_to_your_migrations(
        self, database: Database
    ) -> None:
        assert SavedViews(database).create_table is False

    def test_cleaning_keeps_repeated_values(self) -> None:
        assert clean_query("status=PAID&status=SHIPPED&page=2") == (
            "status=PAID&status=SHIPPED"
        )


class TestPickingColumns:
    def test_only_columns_on_offer_count(self) -> None:
        view = OrderView()

        assert view.pick_columns(["total", "secret", "note"]) == ("total", "note")

    def test_the_order_follows_the_picker(self) -> None:
        view = OrderView()

        assert view.pick_columns(["note", "id"]) == ("id", "note")

    def test_picking_nothing_gives_the_default(self) -> None:
        view = OrderView()

        assert view.pick_columns([]) == ("id", "status", "total")

    def test_the_extras_follow_the_list_columns(self) -> None:
        view = OrderView()

        assert view.get_column_choices() == (
            "id",
            "status",
            "total",
            "note",
            "created_at",
        )


@pytest.fixture
async def client(
    database: Database, store: SavedViews
) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView],
        secret_key="for-the-session",
        saved_views=store,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestColumnsOnThePage:
    async def test_the_picked_columns_are_shown(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?cols=id&cols=note")

        assert headers(page) == ["Id", "Note"]

    async def test_a_column_not_on_offer_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?cols=customer_id&cols=total")

        assert headers(page) == ["Total"]

    async def test_the_pick_is_remembered(self, client: httpx.AsyncClient) -> None:
        await client.get("/admin/orders?cols=id&cols=note")
        again = await client.get("/admin/orders")

        assert headers(again) == ["Id", "Note"]

    async def test_an_empty_pick_goes_back_to_the_default(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.get("/admin/orders?cols=id&cols=note")
        await client.get("/admin/orders?cols=")
        again = await client.get("/admin/orders")

        assert headers(again) == ["Id", "Status", "Total"]

    async def test_the_export_follows_the_columns(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.get("/admin/orders?cols=id&cols=note")
        exported = await client.get("/admin/orders/export")

        assert exported.text.splitlines()[0] == "Id,Note"

    async def test_the_picker_offers_every_choice(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert page.text.count('form="columns-orders" name="cols"') == 5


class TestSavedViewsOnThePage:
    async def test_saving_keeps_the_current_list(
        self, client: httpx.AsyncClient, store: SavedViews
    ) -> None:
        page = await client.get("/admin/orders?status=PAID&page=2")
        saved = await client.post(
            "/admin/orders/saved-views",
            data={
                "name": "Paid",
                "query": "status=PAID&page=2",
                "_csrf": token_in(page),
            },
            follow_redirects=True,
        )

        assert "Saved as Paid." in saved.text
        assert str(saved.url).endswith("/admin/orders?status=PAID")
        kept = await store.visible_to("orders", None)
        assert [(item.name, item.query) for item in kept] == [("Paid", "status=PAID")]

    async def test_the_menu_lists_the_views_and_marks_the_open_one(
        self, client: httpx.AsyncClient, store: SavedViews
    ) -> None:
        await store.save(SavedView("orders", "Paid", "status=PAID"))

        page = await client.get("/admin/orders?status=PAID")

        assert 'href="/admin/orders?status=PAID"' in page.text
        assert re.search(r'aria-current="page"[^>]*>\s*Paid\s*</a>', page.text)

    async def test_a_view_needs_a_name(
        self, client: httpx.AsyncClient, store: SavedViews
    ) -> None:
        page = await client.get("/admin/orders")
        answer = await client.post(
            "/admin/orders/saved-views",
            data={"name": "  ", "query": "", "_csrf": token_in(page)},
            follow_redirects=True,
        )

        assert "Give the view a name." in answer.text
        assert await store.visible_to("orders", None) == []

    async def test_saving_needs_the_form_token(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/admin/orders/saved-views", data={"name": "Paid", "query": ""}
        )

        assert answer.status_code == 403

    async def test_removing_a_view(
        self, client: httpx.AsyncClient, store: SavedViews
    ) -> None:
        saved = await store.save(SavedView("orders", "Paid", "status=PAID"))
        page = await client.get("/admin/orders")
        answer = await client.post(
            f"/admin/orders/saved-views/{saved.id}/delete",
            data={"_csrf": token_in(page)},
            follow_redirects=True,
        )

        assert "View removed." in answer.text
        assert await store.visible_to("orders", None) == []

    async def test_without_a_store_there_is_no_menu(self, database: Database) -> None:
        admin = Admin(database, title="Shop", views=[OrderView])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as plain:
            page = await plain.get("/admin/orders")
            saving = await plain.post(
                "/admin/orders/saved-views", data={"name": "Paid"}
            )

        assert "Save this view" not in page.text
        assert saving.status_code == 404


class TestOwners:
    async def test_people_see_their_own_and_the_shared_views(
        self, database: Database, store: SavedViews
    ) -> None:
        await store.save(SavedView("orders", "Mine", "status=PAID", owner="nima"))
        await store.save(SavedView("orders", "Hers", "status=PENDING", owner="sara"))
        await store.save(
            SavedView("orders", "Team", "status=SHIPPED", owner="sara", shared=True)
        )
        admin = Admin(
            database,
            title="Shop",
            views=[OrderView],
            secret_key="for-the-session",
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            saved_views=store,
        )
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            login = await client.get("/admin/login")
            await client.post(
                "/admin/login",
                data={
                    "username": "nima",
                    "password": "letmein",
                    "_csrf": token_in(login),
                },
            )
            page = await client.get("/admin/orders")

        assert "Mine" in page.text
        assert "Team" in page.text
        assert "Hers" not in page.text
        # Only your own views can be removed.
        assert page.text.count('form="delete-view-') == 1
