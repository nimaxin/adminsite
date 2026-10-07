"""Controls outside the list's table keep the address as it is when they are used.

The table reloads on its own for a search, a sort or a page, and moves the
address with it. What the browser does with `data-keeps` is checked in a
browser; here, that each control carries it, naming the values it sets.
"""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.database import Database
from adminsite.saved_views import SavedViews
from tests.models import Order


class OrderView(ModelView[Order]):
    list_fields = ["id", "status", "total"]
    list_optional_fields = ["note"]
    searchable_fields = ["note"]
    page_size_options = [5, 25]


@pytest.fixture
async def page(database: Database, tmp_path: Path) -> AsyncIterator[str]:
    views = SavedViews(f"sqlite:///{tmp_path / 'views.db'}")
    admin = Admin(
        database, views=[OrderView], secret_key="for-the-session", saved_views=views
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield (await client.get("/admin/orders?sort=-total")).text
    views.close()


def tag(page: str, pattern: str) -> str:
    found = re.search(pattern, page)
    assert found is not None, pattern
    return found.group(0)


class TestEachControlKeepsTheAddress:
    async def test_the_search_sets_only_its_text(self, page: str) -> None:
        assert 'data-keeps="q"' in tag(page, r'<form[^>]*role="search"[^>]*>')

    async def test_the_columns_set_only_the_columns(self, page: str) -> None:
        assert 'data-keeps="cols"' in tag(page, r'<form id="columns-orders"[^>]*>')

    async def test_the_page_size_sets_only_the_size(self, page: str) -> None:
        assert 'data-keeps="size"' in tag(page, r'<form id="sizes-orders"[^>]*>')
        # submit() would skip the submit event the address is kept on.
        assert "this.form.requestSubmit()" in tag(page, r'<select name="size"[^>]*>')

    async def test_the_export_keeps_everything(self, page: str) -> None:
        assert 'data-keeps=""' in tag(
            page, r'<a[^>]*href="/admin/orders/export[^"]*"[^>]*>'
        )

    async def test_a_saved_view_saves_the_address_as_it_is(self, page: str) -> None:
        form = tag(page, r'<form[^>]*action="/admin/orders/saved-views"[^>]*>')

        assert "keptAddress([])" in form
