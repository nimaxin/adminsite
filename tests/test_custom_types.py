"""A column of a type of the project's own reads as the type it wraps.

SQLModel keeps every str of its models in such a type, AutoString, so this is
what lets the search box and a link's picker look in a SQLModel model's text.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import CHAR, DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, UserDefinedType
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.database import Database
from adminsite.fields import (
    DateTimeField,
    StringField,
    TextAreaField,
    UUIDField,
    default_registry,
)
from adminsite.inspector import SQLAlchemyInspector
from tests.support import Backend


class AutoString(TypeDecorator[str]):
    """Text that names no python type, as SQLModel's own AutoString."""

    impl = String
    cache_ok = True


class UTCDateTime(TypeDecorator[datetime]):
    """A time that raises for its python type, as SQLAlchemy 2.0 does."""

    impl = DateTime
    cache_ok = True

    @property
    def python_type(self) -> type[Any]:
        raise NotImplementedError


class GUID(TypeDecorator[UUID]):
    """A UUID kept as 32 characters, which says what it holds."""

    impl = CHAR(32)
    cache_ok = True

    @property
    def python_type(self) -> type[Any]:
        return UUID


class Point(UserDefinedType[str]):
    """A type of the database's own, which names nothing."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "POINT"


class TypesBase(DeclarativeBase):
    pass


class Club(TypesBase):
    __tablename__ = "type_clubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(AutoString(60))
    players: Mapped[list["Player"]] = relationship(back_populates="club")


class Player(TypesBase):
    __tablename__ = "type_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(AutoString(60))
    biography: Mapped[str | None] = mapped_column(AutoString(2000))
    club_id: Mapped[int | None] = mapped_column(ForeignKey("type_clubs.id"))
    club: Mapped[Club | None] = relationship(back_populates="players")


class InspectedBase(DeclarativeBase):
    pass


class Visit(InspectedBase):
    """Only read by the inspector, so it needs no table."""

    __tablename__ = "type_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    token: Mapped[UUID] = mapped_column(GUID)
    place: Mapped[str] = mapped_column(Point)


def kind_of(model: type[Any], name: str) -> type[Any]:
    """The field adminsite picks for a column left to it."""
    return default_registry.field_class_for(
        SQLAlchemyInspector().inspect(model).field_named(name)
    )


class TestTheColumn:
    def test_text_naming_no_type_reads_as_the_text_it_wraps(self) -> None:
        name = SQLAlchemyInspector().inspect(Player).field_named("name")

        assert name.python_type is str
        assert name.max_length == 60
        assert kind_of(Player, "name") is StringField
        assert kind_of(Player, "biography") is TextAreaField

    def test_a_type_raising_for_its_python_type_reads_as_the_type_it_wraps(
        self,
    ) -> None:
        started_at = SQLAlchemyInspector().inspect(Visit).field_named("started_at")

        assert started_at.python_type is datetime
        assert kind_of(Visit, "started_at") is DateTimeField

    def test_a_type_naming_its_python_type_keeps_it(self) -> None:
        assert (
            SQLAlchemyInspector().inspect(Visit).field_named("token").python_type
            is UUID
        )
        assert kind_of(Visit, "token") is UUIDField

    def test_a_type_wrapping_nothing_and_naming_nothing_reads_as_text(self) -> None:
        assert (
            SQLAlchemyInspector().inspect(Visit).field_named("place").python_type is str
        )

    def test_the_type_is_still_a_guess_so_no_kind_is_checked_against_it(
        self,
    ) -> None:
        schema = SQLAlchemyInspector().inspect(Visit)

        assert not schema.field_named("started_at").python_type_known
        assert not schema.field_named("token").python_type_known


class ClubView(ModelView[Club]):
    record_title = "{name}"


class PlayerView(ModelView[Player]):
    searchable_fields = [Player.name]


@pytest.fixture
async def database(backend: Backend) -> AsyncIterator[Database]:
    """The test database with these models' tables, a club and two players."""
    database = backend.database
    async with database.session() as session:
        await session.run(
            lambda plain: TypesBase.metadata.create_all(plain.connection())
        )
        club = Club(name="Harbour Rowers")
        await session.add(club)
        await session.add(Player(name="Lena Fischer", club=club))
        await session.add(Player(name="Marco Rossi"))
        await session.commit()
    yield database
    async with database.session() as session:
        await session.run(lambda plain: TypesBase.metadata.drop_all(plain.connection()))
        await session.commit()


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = Starlette()
    app.mount("/admin", Admin(database, views=[PlayerView, ClubView]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestSearching:
    async def test_the_search_box_looks_in_it(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/players?q=lena")

        assert "Lena Fischer" in page.text
        assert "Marco Rossi" not in page.text

    async def test_a_link_is_picked_by_the_name_it_shows(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/players/lookup/club?q=harb")
        missed = await client.get("/admin/players/lookup/club?q=zzz")

        assert "Harbour Rowers" in found.text
        assert "Harbour Rowers" not in missed.text
