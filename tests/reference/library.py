"""SQLModel models, and views that name their columns as strings.

A type checker takes a SQLModel attribute, `Book.pages`, for the value it
holds, an int, so a setting names the column by its name instead.
"""

import enum
import uuid
from datetime import date
from decimal import Decimal

import sqlmodel
from sqlmodel import Relationship, SQLModel

from adminsite import Field, Inline, ModelView, RefusedError, SaveContext
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
    fields = ["name", "born"]
    searchable_fields = ["name"]
    inlines = [Inline("books", fields=["title", "genre", "pages"])]
    record_title = "{name}"


class BookView(ModelView[Book]):
    fields = [
        "id",
        "title",
        "author",
        "author.born",
        EnumField("genre", tones={Genre.POETRY: "violet"}),
        DecimalField("price", format=EUROS),
        Field("pages", label="Pages"),
        "in_print",
        "published",
    ]
    searchable_fields = ["title", "author.name"]
    sortable_fields = ["title", "price", "published"]
    fields_default_sort = ["-published"]
    list_filters = ["genre", "in_print", "published"]
    record_title = "{title}"

    async def before_save(self, context: SaveContext[Book]) -> None:
        pages = context.values["pages"].get()
        if pages is not None and pages < 1:
            raise RefusedError("A book has at least one page.", field="pages")
