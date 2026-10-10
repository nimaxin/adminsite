"""A SQLModel model is a SQLAlchemy model, so the admin reads it as it is.

Its columns are named through SQLModel's col(), which a type checker reads as
a column; tests/reference/library.py holds the models and the views. The
pages run on every backend.
"""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlmodel import SQLModel, col
from starlette.applications import Starlette

from adminsite import Admin
from adminsite.database import Database
from adminsite.inspector import SQLAlchemyInspector
from adminsite.query import Sort
from tests.reference.library import Author, AuthorView, Book, BookView, Genre
from tests.support import Backend, request_from

BOOK_FORM = {
    "title": "Felicity",
    "genre": "POETRY",
    "price": "15.00",
    "pages": "96",
    "in_print": "on",
    "published": "2015-10-13",
}


class TestSettings:
    def test_col_names_a_column_as_its_attribute_does(self) -> None:
        books = BookView(SQLAlchemyInspector())
        request = request_from()

        assert books._pages.list_fields(request) == (
            "id",
            "title",
            "author",
            "author.born",
            "genre",
            "price",
            "pages",
            "in_print",
            "published",
        )
        assert books._pages.search_paths(request) == ("title", "author.name")
        assert books._pages.default_sort(request) == (
            Sort("published", descending=True),
        )
        assert [item.name for item in books._pages.list_filters(request)] == [
            "genre",
            "in_print",
            "published",
        ]

    def test_an_inline_takes_col_too(self) -> None:
        authors = AuthorView(SQLAlchemyInspector())
        books = authors._inline_views["books"]

        assert books._pages.form_fields(request_from()) == ("title", "genre", "pages")


@pytest.fixture
async def database(backend: Backend) -> AsyncIterator[Database]:
    """The test database with the library's tables, an author and two books."""
    database = backend.database
    async with database.session() as session:
        await session.run(
            lambda plain: SQLModel.metadata.create_all(plain.connection())
        )
        author = Author(name="Mary Oliver", born=date(1935, 9, 10))
        await session.add(author)
        await session.add(
            Book(
                title="Dream Work",
                genre=Genre.POETRY,
                pages=90,
                price=Decimal("14.00"),
                published=date(1986, 1, 1),
                author=author,
            )
        )
        await session.add(
            Book(
                title="Upstream",
                genre=Genre.ESSAYS,
                pages=192,
                price=Decimal("16.00"),
                published=date(2016, 10, 11),
                author=author,
            )
        )
        await session.commit()
    yield database
    async with database.session() as session:
        await session.run(lambda plain: SQLModel.metadata.drop_all(plain.connection()))
        await session.commit()


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = Starlette()
    app.mount("/admin", Admin(database, views=[BookView, AuthorView]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def book_titled(database: Database, title: str) -> Book | None:
    async with database.session() as session:
        found = await session.scalars(select(Book).where(col(Book.title) == title))
        return found.first()


async def author_named(database: Database, name: str) -> Author:
    async with database.session() as session:
        found = await session.scalars(select(Author).where(col(Author.name) == name))
        author: Author | None = found.first()
        assert author is not None
        return author


class TestPages:
    async def test_the_list_shows_each_book_newest_first(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/books")

        assert page.status_code == 200
        assert "Mary Oliver" in page.text
        assert "€14.00" in page.text
        assert page.text.index("Upstream") < page.text.index("Dream Work")

    async def test_a_book_has_a_page(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        book = await book_titled(database, "Dream Work")
        assert book is not None

        page = await client.get(f"/admin/books/{book.id}")

        assert page.status_code == 200
        assert "Dream Work" in page.text
        assert "Mary Oliver" in page.text

    async def test_an_author_is_found_by_the_uuid_the_model_made(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        author = await author_named(database, "Mary Oliver")

        page = await client.get(f"/admin/authors/{author.id}")

        assert page.status_code == 200
        assert "Upstream" in page.text


class TestSearching:
    async def test_the_search_box_looks_in_titles_and_the_authors_name(
        self, client: httpx.AsyncClient
    ) -> None:
        by_title = await client.get("/admin/books?q=dream")
        by_author = await client.get("/admin/books?q=oliver")

        assert "Dream Work" in by_title.text
        assert "Upstream" not in by_title.text
        assert "Dream Work" in by_author.text
        assert "Upstream" in by_author.text

    async def test_an_author_is_picked_by_name(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/books/lookup/author?q=mary")
        missed = await client.get("/admin/books/lookup/author?q=zzz")

        assert "Mary Oliver" in found.text
        assert "Mary Oliver" not in missed.text


class TestSaving:
    async def test_a_new_book_is_saved_with_its_author(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        author = await author_named(database, "Mary Oliver")

        response = await client.post(
            "/admin/books/new", data={**BOOK_FORM, "author": str(author.id)}
        )

        assert response.status_code == 303
        book = await book_titled(database, "Felicity")
        assert book is not None
        assert book.author_id == author.id
        assert book.genre is Genre.POETRY
        assert book.price == Decimal("15.00")
        assert book.published == date(2015, 10, 13)

    async def test_an_edit_is_saved(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        author = await author_named(database, "Mary Oliver")
        book = await book_titled(database, "Dream Work")
        assert book is not None

        response = await client.post(
            f"/admin/books/{book.id}/edit",
            data={
                **BOOK_FORM,
                "title": "Dream Work",
                "pages": "100",
                "author": str(author.id),
            },
        )

        assert response.status_code == 303
        edited = await book_titled(database, "Dream Work")
        assert edited is not None
        assert edited.pages == 100

    async def test_a_title_past_its_max_length_is_refused_on_the_form(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/admin/books/new", data={**BOOK_FORM, "title": "x" * 201}
        )

        assert response.status_code == 422
        assert "Keep this to 200 characters or fewer." in response.text

    async def test_a_hook_reads_the_value_through_col(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post(
            "/admin/books/new", data={**BOOK_FORM, "pages": "0"}
        )

        assert response.status_code == 422
        assert "A book has at least one page." in response.text
        assert await book_titled(database, "Felicity") is None

    async def test_an_author_is_saved_with_a_book_inline(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        response = await client.post(
            "/admin/authors/new",
            data={
                "name": "Wendell Berry",
                "born": "1934-08-05",
                "books-count": "1",
                "books-0-key": "",
                "books-0-title": "Jayber Crow",
                "books-0-genre": "NOVEL",
                "books-0-pages": "363",
            },
        )

        assert response.status_code == 303
        author = await author_named(database, "Wendell Berry")
        book = await book_titled(database, "Jayber Crow")
        assert book is not None
        assert book.author_id == author.id

    async def test_a_book_is_deleted(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        book = await book_titled(database, "Upstream")
        assert book is not None

        response = await client.post(f"/admin/books/{book.id}/delete")

        assert response.status_code == 303
        assert await book_titled(database, "Upstream") is None
