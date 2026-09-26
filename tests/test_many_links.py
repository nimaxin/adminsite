import html
import json
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.models import Article, Tag

TAGS = (
    "Breaking news",
    "Opinion",
    "Science and technology",
    "Travel",
    "Local sport",
    "World politics",
    "Business and markets",
    "Culture",
    "Health",
    "Weather",
)


class ArticleView(ModelView, model=Article):
    form_fields = ("title", "tags")


class TagView(ModelView, model=Tag):
    display_template = "{name}"


@pytest.fixture
async def keys(database: Database) -> dict[str, str]:
    """The ten tags by name, and one article tagged Science, then Breaking."""
    async with database.session() as session:
        tags = {name: Tag(name=name) for name in TAGS}
        for tag in tags.values():
            await session.add(tag)
        article = Article(
            title="Rain on Mars",
            tags=[tags["Science and technology"], tags["Breaking news"]],
        )
        await session.add(article)
        await session.flush()
        found = {name: str(tag.id) for name, tag in tags.items()}
        found["article"] = str(article.id)
        await session.commit()
    return found


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[ArticleView, TagView], secret_key="s")
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def chips(page: httpx.Response) -> list[str]:
    """The names a picker holds, in the order it holds them."""
    found = re.search(r"picked: (\[.*?\])}\)'", page.text)
    assert found is not None
    return [item["label"] for item in json.loads(html.unescape(found.group(1)))]


async def tags_of(database: Database, title: str) -> list[str]:
    async with database.session() as session:
        article = await session.scalar(
            select(Article)
            .where(Article.title == title)
            .options(selectinload(Article.tags))
        )
        assert article is not None
        return [tag.name for tag in article.tags]


async def token_from(client: httpx.AsyncClient, url: str) -> str:
    page = await client.get(url)
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


class TestASmallTable:
    async def test_it_is_a_picker_with_its_records_in_the_page(
        self, client: httpx.AsyncClient, keys: dict[str, str]
    ) -> None:
        page = await client.get(f"/admin/articles/{keys['article']}/edit")

        assert "multiple size" not in page.text
        assert "/lookup/tags" not in page.text
        for name in TAGS:
            assert f'"{name}")' in page.text

    async def test_what_it_holds_shows_in_the_order_it_is_kept(
        self, client: httpx.AsyncClient, keys: dict[str, str]
    ) -> None:
        page = await client.get(f"/admin/articles/{keys['article']}/edit")

        assert chips(page) == ["Science and technology", "Breaking news"]


class TestTheOrderPicked:
    async def test_it_is_the_order_saved_and_read_back(
        self, client: httpx.AsyncClient, database: Database, keys: dict[str, str]
    ) -> None:
        picked = [keys["Weather"], keys["Culture"], keys["Opinion"]]

        answer = await client.post(
            "/admin/articles/new",
            data={
                "_csrf": await token_from(client, "/admin/articles/new"),
                "title": "Picked in turn",
                "tags": picked,
            },
        )

        assert answer.status_code == 303
        assert await tags_of(database, "Picked in turn") == [
            "Weather",
            "Culture",
            "Opinion",
        ]

    async def test_a_failed_save_shows_it_as_sent(
        self, client: httpx.AsyncClient, keys: dict[str, str]
    ) -> None:
        answer = await client.post(
            "/admin/articles/new",
            data={
                "_csrf": await token_from(client, "/admin/articles/new"),
                "title": "",
                "tags": [keys["Weather"], keys["Culture"]],
            },
        )

        assert answer.status_code == 422
        assert chips(answer) == ["Weather", "Culture"]


class TestTheRecordPage:
    async def test_it_names_them_in_the_order_they_are_kept(
        self, client: httpx.AsyncClient, keys: dict[str, str]
    ) -> None:
        page = await client.get(f"/admin/articles/{keys['article']}")

        assert "Science and technology, Breaking news" in page.text
