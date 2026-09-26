import html
import json
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.applications import Starlette

from adminsite import Admin, FieldOptions, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import RelationField
from tests.models import Article, Tag, article_tags

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


class OrderedArticleView(ModelView, model=Article):
    """The same link, where the order the tags are in means something."""

    name = "ordered_articles"
    form_fields = ("title", "tags")
    fields = (FieldOptions("tags", ordered=True),)


@pytest.fixture
async def ordered(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderedArticleView, ArticleView, TagView],
        secret_key="s",
        api=True,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def link_rows(database: Database, article: str) -> list[tuple[int, str]]:
    """The article's link rows, by their serial id, with each tag's name."""
    async with database.session() as session:
        rows = await session.execute(
            select(article_tags.c.id, Tag.name)
            .join(Tag, Tag.id == article_tags.c.tag_id)
            .where(article_tags.c.article_id == int(article))
            .order_by(article_tags.c.id)
        )
        return [(int(key), name) for key, name in rows.all()]


async def save_tags(
    client: httpx.AsyncClient, view: str, article: str, tags: list[str]
) -> httpx.Response:
    url = f"/admin/{view}/{article}/edit"
    return await client.post(
        url,
        data={
            "_csrf": await token_from(client, url),
            "title": "Rain on Mars",
            "tags": tags,
        },
    )


class TestAnOrderedLink:
    async def test_a_new_order_is_saved_and_read_back(
        self, ordered: httpx.AsyncClient, database: Database, keys: dict[str, str]
    ) -> None:
        article = keys["article"]
        breaking, science = keys["Breaking news"], keys["Science and technology"]

        answer = await save_tags(
            ordered, "ordered_articles", article, [breaking, science]
        )
        form = await ordered.get(f"/admin/ordered_articles/{article}/edit")
        page = await ordered.get(f"/admin/ordered_articles/{article}")

        assert answer.status_code == 303
        assert await tags_of(database, "Rain on Mars") == [
            "Breaking news",
            "Science and technology",
        ]
        assert chips(form) == ["Breaking news", "Science and technology"]
        assert "Breaking news, Science and technology" in page.text

    async def test_adding_one_at_the_end_keeps_the_rows_there(
        self, ordered: httpx.AsyncClient, database: Database, keys: dict[str, str]
    ) -> None:
        article = keys["article"]
        before = await link_rows(database, article)

        await save_tags(
            ordered,
            "ordered_articles",
            article,
            [keys["Science and technology"], keys["Breaking news"], keys["Weather"]],
        )

        after = await link_rows(database, article)
        assert after[:2] == before
        assert after[2][1] == "Weather"

    async def test_the_form_lets_its_records_be_put_in_order(
        self, ordered: httpx.AsyncClient, keys: dict[str, str]
    ) -> None:
        form = await ordered.get(f"/admin/ordered_articles/{keys['article']}/edit")

        assert 'draggable="true"' in form.text
        assert "MOVE_UP.replace" in form.text
        assert "MOVE_DOWN.replace" in form.text

    async def test_the_api_writes_the_order_too(
        self, ordered: httpx.AsyncClient, database: Database, keys: dict[str, str]
    ) -> None:
        page = await ordered.get("/admin/ordered_articles")
        found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
        assert found is not None

        answer = await ordered.patch(
            f"/admin/-/api/ordered_articles/{keys['article']}",
            json={"tags": [keys["Weather"], keys["Science and technology"]]},
            headers={"X-CSRF-Token": found.group(1)},
        )

        assert answer.status_code == 200, answer.text
        assert await tags_of(database, "Rain on Mars") == [
            "Weather",
            "Science and technology",
        ]


class TestALinkThatIsNotOrdered:
    async def test_a_new_order_leaves_it_as_it_was(
        self, ordered: httpx.AsyncClient, database: Database, keys: dict[str, str]
    ) -> None:
        breaking, science = keys["Breaking news"], keys["Science and technology"]

        await save_tags(ordered, "articles", keys["article"], [breaking, science])

        assert await tags_of(database, "Rain on Mars") == [
            "Science and technology",
            "Breaking news",
        ]

    def test_a_link_to_one_record_cannot_be_ordered(self) -> None:
        with pytest.raises(AdminSiteError, match="has no order"):
            RelationField("tag", target=Tag, ordered=True)
