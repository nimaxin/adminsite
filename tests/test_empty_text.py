from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Field, ModelView
from adminsite._importing import normalize
from adminsite.database import Database
from adminsite.fields import RelationField
from tests.models import Article, Product, Tag


class ProductView(ModelView[Product]):
    fields = [Product.name, Field(Product.description, empty_text="No description")]


class ArticleView(ModelView[Article]):
    fields = [Article.title, RelationField(Article.tags, empty_text="Every tag")]


class TagView(ModelView[Tag]):
    record_title = "{name}"


@pytest.fixture
async def articles(database: Database) -> dict[str, str]:
    """One article tagged Science and one with no tags, by title."""
    async with database.session() as session:
        tagged = Article(title="Rain on Mars", tags=[Tag(name="Science")])
        untagged = Article(title="Letters")
        await session.add(tagged)
        await session.add(untagged)
        await session.flush()
        keys = {"tagged": str(tagged.id), "untagged": str(untagged.id)}
        await session.commit()
    return keys


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[ProductView, ArticleView, TagView], secret_key="s")
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestAnEmptyValue:
    async def test_the_list_shows_the_text(
        self, client: httpx.AsyncClient, articles: dict[str, str]
    ) -> None:
        products = await client.get("/admin/products")
        listed = await client.get("/admin/articles")

        assert "No description" in products.text
        assert "Every tag" in listed.text
        assert "Science" in listed.text

    async def test_the_record_page_shows_the_text(
        self, client: httpx.AsyncClient, articles: dict[str, str]
    ) -> None:
        product = await client.get("/admin/products/1")
        untagged = await client.get(f"/admin/articles/{articles['untagged']}")
        tagged = await client.get(f"/admin/articles/{articles['tagged']}")

        assert "No description" in product.text
        assert "Every tag" in untagged.text
        assert "Science" in tagged.text
        assert "Every tag" not in tagged.text

    async def test_the_export_writes_the_text(self, client: httpx.AsyncClient) -> None:
        export = await client.get("/admin/products/export?format=csv")

        assert "Linen shirt,No description" in export.text

    async def test_the_form_is_unchanged(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/products/1/edit")

        assert "No description" not in form.text

    def test_an_import_reads_it_back_as_empty(self) -> None:
        item = ProductView()._fields.field_for("description")

        assert normalize(item, "no description") == ""
        assert normalize(item, "Made of linen") == "Made of linen"
