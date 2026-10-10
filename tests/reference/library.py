"""Views of SQLModel models, each column named through SQLModel's `col()`.

A type checker takes a SQLModel attribute, `Book.pages`, for the value it
holds. `col(Book.pages)` is how SQLModel's own queries say it is a column, and
the settings take it as they take a SQLAlchemy model's attribute.
"""

import enum
import uuid
from datetime import date
from decimal import Decimal
from typing import assert_type

import sqlmodel
from sqlmodel import Relationship, SQLModel, col

from adminsite import (
    Descending,
    Field,
    Inline,
    Link,
    ModelView,
    RefusedError,
    SaveContext,
)
from adminsite.fields import DecimalField, EnumField

EUROS = "€{:,.2f}"


class Genre(enum.Enum):
    NOVEL = "novel"
    POETRY = "poetry"
    ESSAYS = "essays"


class Author(SQLModel, table=True):
    id: uuid.UUID = sqlmodel.Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = sqlmodel.Field(max_length=120)
    born: date | None = None
    books: list["Book"] = Relationship(back_populates="author")

    def __str__(self) -> str:
        return self.name


class Book(SQLModel, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)
    title: str = sqlmodel.Field(max_length=200)
    genre: Genre = Genre.NOVEL
    pages: int | None = None
    price: Decimal = sqlmodel.Field(default=Decimal(0), max_digits=8, decimal_places=2)
    in_print: bool = True
    published: date | None = None
    author_id: uuid.UUID | None = sqlmodel.Field(default=None, foreign_key="author.id")
    author: Author | None = Relationship(back_populates="books")


class AuthorView(ModelView[Author]):
    fields = [col(Author.name), col(Author.born)]
    searchable_fields = [col(Author.name)]
    inlines = [
        Inline(
            col(Author.books),
            fields=[col(Book.title), col(Book.genre), col(Book.pages)],
        )
    ]
    record_title = "{name}"


class BookView(ModelView[Book]):
    fields = [
        col(Book.id),
        col(Book.title),
        col(Book.author),
        Link(col(Book.author), col(Author.born)),
        EnumField(col(Book.genre), tones={Genre.POETRY: "violet"}),
        DecimalField(col(Book.price), format=EUROS),
        Field(col(Book.pages), label="Pages"),
        col(Book.in_print),
        col(Book.published),
    ]
    searchable_fields = [col(Book.title), Link(col(Book.author), col(Author.name))]
    sortable_fields = [col(Book.title), col(Book.price), col(Book.published)]
    fields_default_sort = [Descending(col(Book.published))]
    list_filters = [col(Book.genre), col(Book.in_print), col(Book.published)]
    record_title = "{title}"

    async def before_save(self, context: SaveContext[Book]) -> None:
        pages = context.values[col(Book.pages)].get()
        assert_type(pages, int | None)
        if pages is not None and pages < 1:
            raise RefusedError("A book has at least one page.", field=col(Book.pages))
