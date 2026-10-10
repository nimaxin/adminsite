"""A SQLModel model is a SQLAlchemy model, so the admin reads it as it is.

What is SQLModel's own is tested here: a str column, which SQLModel keeps in
its AutoString, and a key its default_factory makes. tests/reference/library.py
holds the models and the views. Each test runs on every backend.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlmodel import SQLModel
from starlette.applications import Starlette

from adminsite import Admin
from adminsite.database import Database
from tests.reference.library import Author, AuthorView, Book, BookView
from tests.support import Backend


@pytest.fixture
async def database(backend: Backend) -> AsyncIterator[Database]:
    """The test database with the library's tables, an author and two books."""
    database = backend.database
    async with database.session() as session:
        await session.run(
            lambda plain: SQLModel.metadata.create_all(plain.connection())
        )
        author = Author(name="Mary Oliver")
        await session.add(author)
        await session.add(Book(title="Dream Work", author=author))
        await session.add(Book(title="Upstream", author=author))
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
        found = await session.scalars(select(Book).filter_by(title=title))
        book: Book | None = found.first()
        return book


async def author_named(database: Database, name: str) -> Author:
    async with database.session() as session:
        found = await session.scalars(select(Author).filter_by(name=name))
        author: Author | None = found.first()
        assert author is not None
        return author


class TestText:
    async def test_the_search_box_looks_in_a_str_column(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/books?q=dream")

        assert "Dream Work" in page.text
        assert "Upstream" not in page.text

    async def test_an_author_is_picked_by_name(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/books/lookup/author?q=mary")
        missed = await client.get("/admin/books/lookup/author?q=zzz")

        assert "Mary Oliver" in found.text
        assert "Mary Oliver" not in missed.text


class TestSaving:
    async def test_a_new_author_gets_the_key_its_model_makes(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/authors/new")
        response = await client.post(
            "/admin/authors/new", data={"name": "Wendell Berry"}
        )

        assert 'name="id"' not in form.text
        assert response.status_code == 303
        author = await author_named(database, "Wendell Berry")
        assert response.headers["location"].endswith(f"/admin/authors/{author.id}")
        page = await client.get(f"/admin/authors/{author.id}")
        assert "Wendell Berry" in page.text

    async def test_a_new_book_is_saved_with_its_author(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        author = await author_named(database, "Mary Oliver")

        response = await client.post(
            "/admin/books/new", data={"title": "Felicity", "author": str(author.id)}
        )

        assert response.status_code == 303
        book = await book_titled(database, "Felicity")
        assert book is not None
        assert book.author_id == author.id

    async def test_an_edit_is_saved(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        author = await author_named(database, "Mary Oliver")
        book = await book_titled(database, "Dream Work")
        assert book is not None

        response = await client.post(
            f"/admin/books/{book.id}/edit",
            data={"title": "Dream Work (1986)", "author": str(author.id)},
        )

        assert response.status_code == 303
        assert await book_titled(database, "Dream Work (1986)") is not None
