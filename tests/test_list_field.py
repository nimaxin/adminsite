import os
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import ARRAY, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database, SQLAlchemyInspector
from adminsite.exceptions import FieldValidationError
from adminsite.fields import (
    IntegerField,
    JSONField,
    ListField,
    StringField,
    default_registry,
)
from adminsite.imports import normalize
from tests.models import Setting

POSTGRES_URL = os.environ.get("ADMINSITE_POSTGRES_URL", "")


class ListBase(DeclarativeBase):
    pass


class Region(ListBase):
    """Array columns live on Postgres alone, so this model has a base of its own."""

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    codes: Mapped[list[str]] = mapped_column(ARRAY(String(2)), default=list)
    scores: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    grid: Mapped[list[list[int]] | None] = mapped_column(
        ARRAY(Integer, dimensions=2), nullable=True
    )

    def __str__(self) -> str:
        return self.name


class RegionView(ModelView, model=Region):
    list_display = ("name", "codes")
    form_fields = ("name", "codes", "scores")


class TestTheColumn:
    def test_the_inspector_says_what_each_value_is(self) -> None:
        schema = SQLAlchemyInspector().inspect(Region)

        codes = schema.field_named("codes")
        assert codes.python_type is list
        assert codes.item is not None
        assert codes.item.python_type is str
        assert codes.item.max_length == 2

    def test_an_array_gets_a_list_of_the_right_values(self) -> None:
        schema = SQLAlchemyInspector().inspect(Region)

        codes = default_registry.build(schema.field_named("codes"))
        scores = default_registry.build(schema.field_named("scores"))

        assert isinstance(codes, ListField)
        assert isinstance(codes.item, StringField)
        assert codes.item.max_length == 2
        assert isinstance(scores, ListField)
        assert isinstance(scores.item, IntegerField)

    def test_an_empty_list_is_a_value_so_it_is_never_required(self) -> None:
        schema = SQLAlchemyInspector().inspect(Region)

        assert not default_registry.build(schema.field_named("codes")).required

    def test_an_array_of_arrays_stays_json(self) -> None:
        schema = SQLAlchemyInspector().inspect(Region)

        assert isinstance(default_registry.build(schema.field_named("grid")), JSONField)


class TestReadingAndWriting:
    def test_each_line_is_one_value(self) -> None:
        field = ListField("scores", item=IntegerField("scores"))

        assert field.parse("3\r\n\r\n 1 \n") == [3, 1]

    def test_empty_is_an_empty_list(self) -> None:
        assert ListField("codes").parse("") == []
        assert ListField("codes").parse(None) == []

    def test_a_required_list_needs_a_value(self) -> None:
        with pytest.raises(FieldValidationError, match="required"):
            ListField("codes", required=True).parse("\n")

    def test_a_bad_value_names_its_line(self) -> None:
        field = ListField("scores", item=IntegerField("scores"))

        with pytest.raises(FieldValidationError) as caught:
            field.parse("3\n\nthree")

        assert caught.value.message == "Line 3: Enter a whole number."

    def test_the_box_holds_one_value_per_line_and_a_cell_one_line(self) -> None:
        field = ListField("codes")

        assert field.serialize(["US", "CA"]) == "US\nCA"
        assert field.display(["US", "CA"]) == "US, CA"
        assert field.serialize(None) == field.display(None) == ""

    def test_a_file_can_separate_them_with_commas_as_the_export_does(self) -> None:
        field = ListField("codes")

        assert field.parse(normalize(field, "US, CA")) == ["US", "CA"]
        assert field.parse(normalize(field, "US\nCA")) == ["US", "CA"]


@pytest.fixture
async def postgres() -> AsyncIterator[Database]:
    if not POSTGRES_URL:
        pytest.skip("Array columns need Postgres: set ADMINSITE_POSTGRES_URL.")
    engine = create_async_engine(
        POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    async with engine.begin() as connection:
        await connection.run_sync(ListBase.metadata.drop_all)
        await connection.run_sync(ListBase.metadata.create_all)
    async with AsyncSession(engine) as session:
        session.add(Region(name="North America", codes=["US", "CA"], scores=[3, 1]))
        await session.commit()
    yield Database(engine)
    async with engine.begin() as connection:
        await connection.run_sync(ListBase.metadata.drop_all)
    await engine.dispose()


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(postgres: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(postgres, views=[RegionView], secret_key="for-the-session", api=True)
    async with serve(admin) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


async def region(postgres: Database) -> Region:
    async with postgres.session() as session:
        found = await session.get(Region, 1)
        assert found is not None
        return found


class TestOnPostgres:
    async def test_the_form_shows_one_value_per_line(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/regions/1/edit")

        assert re.search(r'name="codes"[^>]*>US\nCA</textarea>', page.text)
        assert "One value per line." in page.text

    async def test_saving_reads_each_line(
        self, client: httpx.AsyncClient, postgres: Database
    ) -> None:
        form = await client.get("/admin/regions/1/edit")
        answer = await client.post(
            "/admin/regions/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "Europe",
                "codes": "DE\r\nFR\r\n\r\nIT",
                "scores": "",
            },
        )

        assert answer.status_code == 303
        saved = await region(postgres)
        assert saved.codes == ["DE", "FR", "IT"]
        assert saved.scores == []

    async def test_a_bad_line_is_named_and_nothing_is_saved(
        self, client: httpx.AsyncClient, postgres: Database
    ) -> None:
        form = await client.get("/admin/regions/1/edit")
        answer = await client.post(
            "/admin/regions/1/edit",
            data={"_csrf": token_in(form), "name": "Europe", "codes": "DE\nFRA"},
        )

        assert answer.status_code == 422
        assert "Line 2: Keep this to 2 characters or fewer." in answer.text
        assert (await region(postgres)).codes == ["US", "CA"]

    async def test_the_list_shows_them_on_one_line(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/regions")

        assert "US, CA" in page.text

    async def test_the_api_reads_and_writes_a_list(
        self, client: httpx.AsyncClient, postgres: Database
    ) -> None:
        page = await client.get("/admin/regions")
        client.headers["X-CSRF-Token"] = token_in(page)

        read = await client.get("/admin/-/api/regions/1")
        changed = await client.patch("/admin/-/api/regions/1", json={"codes": ["DE"]})

        assert read.json()["codes"] == ["US", "CA"]
        assert changed.status_code == 200
        assert (await region(postgres)).codes == ["DE"]


class NotesView(ModelView, model=Setting):
    form_fields = ("name", "notes")
    fields = (ListField("notes"),)


class TestAJsonColumnHoldingAList:
    async def test_it_can_be_edited_as_a_list_on_any_database(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[NotesView], secret_key="for-the-session")
        async with serve(admin) as client:
            form = await client.get("/admin/settings/1/edit")
            answer = await client.post(
                "/admin/settings/1/edit",
                data={
                    "_csrf": token_in(form),
                    "name": "delivery",
                    "notes": "Leave it at the door\nRing twice",
                },
            )

        assert answer.status_code == 303
        async with database.session() as session:
            setting = await session.get(Setting, 1)
            assert setting is not None
            stored: Any = setting.notes
        assert stored == ["Leave it at the door", "Ring twice"]
