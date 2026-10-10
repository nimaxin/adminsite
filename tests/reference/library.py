"""SQLModel models, and views that name their columns as strings.

A type checker takes a SQLModel attribute for the value it holds, not for a
column, so a setting names each column by its name.
"""

import uuid

from sqlmodel import Field, Relationship, SQLModel

from adminsite import ModelView


class Author(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=120)


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)
    author_id: uuid.UUID | None = Field(default=None, foreign_key="author.id")
    author: Author | None = Relationship()


class AuthorView(ModelView[Author]):
    record_title = "{name}"


class BookView(ModelView[Book]):
    searchable_fields = ["title"]
