import html
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import FieldValidationError
from adminsite.fields import JSONField, default_registry
from adminsite.schema import FieldSchema
from tests.models import Setting


class SettingView(ModelView, model=Setting):
    list_display = ("name", "options")
    form_fields = ("name", "options", "notes")


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[SettingView], secret_key="for-the-session", api=True)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


async def options_of(database: Database, key: int = 1) -> dict[str, Any]:
    async with database.session() as session:
        setting = await session.get(Setting, key)
        assert setting is not None
        return setting.options


class TestTheField:
    def test_a_json_column_gets_it(self) -> None:
        view = SettingView()

        assert isinstance(view.field_for("options"), JSONField)
        assert view.field_for("options").widget == "json"

    def test_it_is_chosen_by_the_registry(self) -> None:
        schema = FieldSchema(name="options", label="Options", python_type=dict)

        assert default_registry.field_class_for(schema) is JSONField

    def test_a_document_reads_on_one_line(self) -> None:
        field = JSONField("options")

        assert field.display({"a": 1, "b": [2, 3]}) == '{"a": 1, "b": [2, 3]}'
        assert field.display(None) == ""

    def test_a_long_document_is_cut_short(self) -> None:
        field = JSONField("options")

        shown = field.display({"key": "x" * 300})

        assert len(shown) == 120
        assert shown.endswith("…")

    def test_the_box_gets_it_laid_out(self) -> None:
        field = JSONField("options")

        assert field.serialize({"a": 1}) == '{\n  "a": 1\n}'

    def test_it_reads_a_document_back(self) -> None:
        field = JSONField("options")

        assert field.parse('{"a": [1, 2]}') == {"a": [1, 2]}
        assert field.parse("[1, 2]") == [1, 2]
        assert field.parse("  ") is None

    def test_a_missing_brace_is_a_field_error(self) -> None:
        field = JSONField("options")

        with pytest.raises(FieldValidationError, match="Write valid JSON"):
            field.parse('{"a": 1')

    def test_text_is_kept_as_written(self) -> None:
        field = JSONField("options")

        assert field.serialize({"naam": "Renée"}) == '{\n  "naam": "Renée"\n}'


class TestTheForm:
    async def test_a_document_goes_there_and_back(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/settings/1/edit")
        answer = await client.post(
            "/admin/settings/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "delivery",
                "options": '{"carriers": ["dhl"], "free_over": 75}',
                "notes": "",
            },
        )

        assert answer.status_code == 303
        assert await options_of(database) == {"carriers": ["dhl"], "free_over": 75}

    async def test_the_box_shows_it_laid_out(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/settings/1/edit")

        assert "&#34;carriers&#34;: [" in form.text
        assert 'class="textarea h-40 w-full font-mono' in form.text

    async def test_a_malformed_document_comes_back_with_the_text(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/settings/1/edit")
        answer = await client.post(
            "/admin/settings/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "delivery",
                "options": '{"carriers": ["dhl"',
                "notes": "",
            },
        )

        assert answer.status_code == 422
        assert "Write valid JSON" in answer.text
        assert "[&#34;dhl&#34;" in answer.text
        assert await options_of(database) == {
            "carriers": ["dhl", "ups"],
            "free_over": 50,
        }

    async def test_the_list_shows_it_on_one_line(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/settings")

        shown = html.unescape(listed.text)

        assert '{"carriers": ["dhl", "ups"], "free_over": 50}' in shown

    async def test_nothing_stays_nothing(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/settings/1/edit")
        await client.post(
            "/admin/settings/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "delivery",
                "options": "{}",
                "notes": "",
            },
        )

        async with database.session() as session:
            setting = await session.get(Setting, 1)
            assert setting is not None
            assert setting.notes is None


class TestTheApi:
    async def test_it_reads_and_writes_documents(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/settings")
        read = (await client.get("/admin/-/api/settings/1")).json()
        written = await client.patch(
            "/admin/-/api/settings/1",
            json={"options": {"free_over": 10}},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert read["options"] == {"carriers": ["dhl", "ups"], "free_over": 50}
        assert written.status_code == 200
        assert await options_of(database) == {"free_over": 10}

    async def test_a_document_sent_as_text_is_read_too(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/settings")
        answer = await client.patch(
            "/admin/-/api/settings/1",
            json={"options": '{"free_over": 20}'},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert answer.status_code == 200
        assert await options_of(database) == {"free_over": 20}
