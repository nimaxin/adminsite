import html
import json
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

import httpx
import pytest
from pydantic import BaseModel, Field, field_validator
from starlette.applications import Starlette
from typing_extensions import TypedDict

from adminsite import Admin, ModelView
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError, FieldValidationError
from adminsite.fields import JSONField
from adminsite.fields._documents.document import Document, DocumentError
from adminsite.fields._documents.inputs import (
    DocumentChoice,
    DocumentCode,
    DocumentInteger,
    DocumentNumber,
    DocumentSwitch,
)
from adminsite.fields._documents.shapes import Fixed, Group, Pairs, Rows, Value
from tests.models import Setting
from tests.test_accessibility import Controls


class Zone(TypedDict):
    name: str
    days: Annotated[int, Field(ge=1)]


class Delivery(BaseModel):
    carriers: list[Literal["dhl", "ups", "post"]] = []
    free_over: Annotated[
        float, Field(ge=0, description="Orders above this ship free.")
    ] = 0
    express: bool = False
    zones: list[Zone] = []
    fees: dict[Literal["small", "large"], Annotated[float, Field(gt=0)]] = {}

    @field_validator("free_over")
    @classmethod
    def not_seven(cls, value: float) -> float:
        if value == 7:
            raise ValueError("Seven is an unlucky threshold.")
        return value


class SettingView(ModelView[Setting]):
    fields = ["name", JSONField("options", schema=Delivery), "notes"]
    exclude_fields_from_list = ["notes"]


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


def serve(view: type[ModelView[Any]], database: Database) -> httpx.AsyncClient:
    admin = Admin(database, views=[view], secret_key="for-the-session", api=True)
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(SettingView, database) as client:
        yield client


async def options_of(database: Database, key: int = 1) -> Any:
    async with database.session() as session:
        setting = await session.get(Setting, key)
        assert setting is not None
        return setting.options


def filled(**options: Any) -> dict[str, Any]:
    """A delivery form, as the browser sends it, with some inputs changed."""
    form = {
        "name": "delivery",
        "options~form": "1",
        "options.carriers": ["ups", "dhl"],
        "options.free_over": "40",
        "options.express": "true",
        "options.zones.0": "",
        "options.zones.0.name": "North",
        "options.zones.0.days": "2",
        "options.fees.0": "",
        "options.fees.0.key": "small",
        "options.fees.0.value": "4.5",
        "notes": "",
    }
    form.update(options)
    return {key: value for key, value in form.items() if value is not None}


async def save(
    client: httpx.AsyncClient, form: dict[str, Any], key: int = 1
) -> httpx.Response:
    page = await client.get(f"/admin/settings/{key}/edit")
    return await client.post(
        f"/admin/settings/{key}/edit", data={"_csrf": token_in(page), **form}
    )


class TestTheShapes:
    def test_each_part_gets_the_shape_its_type_calls_for(self) -> None:
        shape = Document(Delivery).shape

        assert isinstance(shape, Group)
        kinds = {part.key: part.shape for part in shape.properties}
        assert isinstance(kinds["carriers"], Value)
        assert isinstance(kinds["carriers"].field, DocumentChoice)
        assert kinds["carriers"].field.multiple
        assert isinstance(kinds["free_over"], Value)
        assert isinstance(kinds["free_over"].field, DocumentNumber)
        assert isinstance(kinds["express"], Value)
        assert isinstance(kinds["express"].field, DocumentSwitch)
        assert isinstance(kinds["zones"], Rows)
        assert isinstance(kinds["fees"], Pairs)
        assert kinds["fees"].keys == ("small", "large")

    def test_labels_read_as_sentences_and_a_title_of_its_own_is_kept(self) -> None:
        class Shop(BaseModel):
            free_shipping_over: float = 0
            vat: float = Field(0, title="VAT rate")

        shape = Document(Shop).shape

        assert isinstance(shape, Group)
        assert [part.label for part in shape.properties] == [
            "Free shipping over",
            "VAT rate",
        ]

    def test_the_description_and_the_limits_are_the_note(self) -> None:
        shape = Document(Delivery).shape
        assert isinstance(shape, Group)
        free_over = shape.properties[1].shape

        assert isinstance(free_over, Value)
        assert free_over.field.hint() == "Orders above this ship free. 0 or more."

    def test_a_choice_between_shapes_is_written_as_json(self) -> None:
        class Percent(BaseModel):
            percent: int

        class Amount(BaseModel):
            amount: float

        class Promotion(BaseModel):
            kind: Literal["promotion"] = "promotion"
            off: Percent | Amount

        shape = Document(Promotion).shape

        assert isinstance(shape, Group)
        kind, off = shape.properties
        assert isinstance(kind.shape, Fixed)
        assert isinstance(off.shape, Value)
        assert isinstance(off.shape.field, DocumentCode)

    def test_a_model_that_holds_itself_does_not_read_for_ever(self) -> None:
        class Folder(BaseModel):
            name: str
            children: list["Folder"] = []

        shape = Document(Folder).shape

        assert isinstance(shape, Group)
        assert isinstance(shape.properties[1].shape, Value)

    def test_a_json_schema_dict_works_too(self) -> None:
        shape = Document(
            {
                "type": "object",
                "properties": {
                    "retries": {"type": "integer", "minimum": 0, "maximum": 5},
                    "mode": {"enum": ["fast", "safe"]},
                },
                "required": ["mode"],
            }
        ).shape

        assert isinstance(shape, Group)
        retries, mode = shape.properties
        assert isinstance(retries.shape, Value)
        assert isinstance(retries.shape.field, DocumentInteger)
        assert retries.shape.field.hint() == "0 to 5."
        assert isinstance(mode.shape, Value)
        assert mode.shape.field.required

    def test_a_schema_adminsite_cannot_read_stops_the_start(self) -> None:
        with pytest.raises(AdminSiteError, match="cannot read"):
            JSONField("options", schema=object())

    def test_partial_needs_an_object(self) -> None:
        with pytest.raises(AdminSiteError, match="partial"):
            JSONField("options", schema=list[int], partial=True)


class TestReadingAForm:
    def test_the_inputs_read_back_into_the_document(self) -> None:
        document = Document(Delivery, name="options")

        read = document.read(filled(), "options")

        assert read == {
            "carriers": ["ups", "dhl"],
            "free_over": 40.0,
            "express": True,
            "zones": [{"name": "North", "days": 2}],
            "fees": {"small": 4.5},
        }

    def test_a_document_goes_to_inputs_and_back(self) -> None:
        document = Document(Delivery, name="options")
        stored = {
            "carriers": ["post"],
            "free_over": 12.5,
            "express": False,
            "zones": [{"name": "South", "days": 4}],
            "fees": {"large": 9.0},
        }

        values = document.form_values(stored, "options")

        assert document.read({**values, "options~form": "1"}, "options") == stored

    def test_a_row_left_empty_is_not_a_row(self) -> None:
        document = Document(Delivery, name="options")

        read = document.read(
            filled(**{"options.zones.1": "", "options.zones.1.name": ""}), "options"
        )

        assert read["zones"] == [{"name": "North", "days": 2}]

    def test_a_problem_found_by_pydantic_lands_on_its_row(self) -> None:
        document = Document(Delivery, name="options")
        form = filled(
            **{
                "options.zones.4": "",
                "options.zones.4.name": "West",
                "options.zones.4.days": "0",
            }
        )

        with pytest.raises(DocumentError) as raised:
            document.read(form, "options")

        assert raised.value.errors == {"options.zones.4.days": "Enter 1 or more."}

    def test_a_key_given_twice_is_refused(self) -> None:
        document = Document(Delivery, name="options")
        form = filled(
            **{
                "options.fees.1": "",
                "options.fees.1.key": "small",
                "options.fees.1.value": "2",
            }
        )

        with pytest.raises(DocumentError) as raised:
            document.read(form, "options")

        assert raised.value.errors == {"options.fees.1.key": "This key is given twice."}

    def test_the_applications_own_validator_speaks(self) -> None:
        document = Document(Delivery, name="options")

        with pytest.raises(DocumentError) as raised:
            document.read(filled(**{"options.free_over": "7"}), "options")

        assert raised.value.errors == {
            "options.free_over": "Seven is an unlucky threshold."
        }

    def test_keys_the_schema_does_not_name_are_kept(self) -> None:
        document = Document(
            {"type": "object", "properties": {"mode": {"type": "string"}}},
            name="options",
        )

        read = document.read(
            {"options~form": "1", "options.mode": "fast"},
            "options",
            stored={"mode": "safe", "legacy": 1},
        )

        assert read == {"mode": "fast", "legacy": 1}

    def test_a_key_with_a_dot_has_an_input_of_its_own(self) -> None:
        document = Document(
            {"type": "object", "properties": {"a.b": {"type": "string"}}},
            name="options",
        )

        values = document.form_values({"a.b": "x"}, "options")

        assert values == {"options.a%2Eb": "x"}


class TestTheForm:
    async def test_it_is_drawn_from_the_schema(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/settings/1/edit")

        assert 'name="options~form"' in page.text
        assert re.search(r'name="options\.free_over"[^>]*value="50"', page.text)
        assert 'name="options.express"' in page.text
        assert "Orders above this ship free. 0 or more." in page.text
        assert re.search(r'name="options\.fees\.__index__\.key"', page.text)
        assert 'hx-post="/admin/settings/1/document/options"' in page.text

    async def test_every_input_has_a_name(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/settings/1/edit")
        controls = Controls()
        controls.feed(page.text)

        assert controls.unnamed() == []

    async def test_a_new_record_starts_from_the_defaults(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/settings/new")

        assert re.search(r'name="options\.free_over"[^>]*value="0"', page.text)

    async def test_saving_writes_the_document(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await save(client, filled())

        assert answer.status_code == 303
        assert await options_of(database) == {
            "carriers": ["ups", "dhl"],
            "free_over": 40.0,
            "express": True,
            "zones": [{"name": "North", "days": 2}],
            "fees": {"small": 4.5},
        }

    async def test_a_problem_shows_beside_its_input_and_nothing_is_saved(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await save(
            client,
            filled(**{"options.free_over": "-5", "options.zones.0.name": ""}),
        )
        shown = html.unescape(answer.text)

        assert answer.status_code == 422
        assert re.search(r'name="options\.free_over"[^>]*value="-5"', answer.text)
        assert "Enter 0 or more." in shown
        assert "Options, Free over" in shown
        assert "Options, Zones, row 1, Name" in shown
        assert await options_of(database) == {
            "carriers": ["dhl", "ups"],
            "free_over": 50,
        }

    async def test_a_value_that_does_not_fit_is_edited_as_json(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            setting = await session.get(Setting, 2)
            assert setting is not None
            setting.options = ["not", "an", "object"]  # type: ignore[assignment]
            await session.commit()

        page = await client.get("/admin/settings/2/edit")

        assert 'name="options~form"' not in page.text
        assert 'x-data="jsonEditor()"' in page.text


class TestTheJSONView:
    async def test_it_shows_what_save_would_write(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/settings/1/edit")

        answer = await client.post(
            "/admin/settings/1/document/options",
            data={"_csrf": token_in(page), **filled()},
        )
        shown = html.unescape(answer.text)

        assert answer.status_code == 200
        assert '"free_over"' in shown
        assert "40.0" in shown

    async def test_it_says_what_needs_another_look(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/settings/new")

        answer = await client.post(
            "/admin/settings/document/options",
            data={"_csrf": token_in(page), **filled(**{"options.free_over": "x"})},
        )

        assert "Correct these in the form first:" in answer.text
        assert "Free over: Enter a number." in answer.text

    async def test_it_needs_the_forms_token(self, client: httpx.AsyncClient) -> None:
        answer = await client.post("/admin/settings/1/document/options", data=filled())

        assert answer.status_code == 403

    async def test_a_field_without_a_schema_has_none(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/settings/1/edit")

        answer = await client.post(
            "/admin/settings/1/document/notes", data={"_csrf": token_in(page)}
        )

        assert answer.status_code == 404


class TestTheRecordPage:
    async def test_each_value_is_named_by_its_title(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await save(client, filled())

        page = await client.get("/admin/settings/1")
        shown = html.unescape(page.text)

        assert "Free over</dt>" in shown
        assert "Ups, Dhl</dd>" in shown
        assert re.search(r"<td[^>]*>North</td>", shown)
        assert re.search(r"<td[^>]*>Small</td>", shown)
        assert "Values</button>" in shown
        assert 'class="json-view' in page.text

    async def test_a_value_the_document_lacks_reads_not_set(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/settings/1")

        assert "Not set" in page.text


class TestTheApi:
    async def test_a_document_is_checked_part_by_part(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/settings")

        answer = await client.patch(
            "/admin/-/api/settings/1",
            json={"options": {"free_over": -1, "zones": [{"name": "North"}]}},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert answer.status_code == 422
        assert answer.json()["errors"] == {
            "options.free_over": "Enter 0 or more.",
            "options.zones.0.days": "This field is required.",
        }

    async def test_a_document_is_saved_as_the_schema_writes_it(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/settings")

        answer = await client.patch(
            "/admin/-/api/settings/1",
            json={"options": {"free_over": 10}},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert answer.status_code == 200
        assert await options_of(database) == {
            "carriers": [],
            "free_over": 10.0,
            "express": False,
            "zones": [],
            "fees": {},
        }


class TestTheCodeBox:
    def test_text_is_checked_against_the_schema_in_one_message(self) -> None:
        field = JSONField("options", schema=Delivery)

        with pytest.raises(FieldValidationError, match=r"Free over: Enter 0 or more\."):
            field.parse('{"free_over": -1}')


class Shop(BaseModel):
    currency: Literal["EUR", "USD"] = "EUR"
    vat: Annotated[float, Field(ge=0, le=100)] = 21


def schema_for(setting: Setting) -> Any:
    """Each setting's value has the shape its name gives it."""
    return {"delivery": Delivery, "shop": Shop}.get(setting.name)


class PerNameView(ModelView[Setting]):
    fields = ["name", JSONField("options", schema=schema_for)]


@pytest.fixture
async def per_name(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(PerNameView, database) as client:
        yield client


class TestASchemaFromTheRecord:
    async def test_two_records_open_as_two_forms(
        self, per_name: httpx.AsyncClient
    ) -> None:
        delivery = await per_name.get("/admin/settings/1/edit")
        shop = await per_name.get("/admin/settings/2/edit")

        assert 'name="options.free_over"' in delivery.text
        assert 'name="options.currency"' not in delivery.text
        assert 'name="options.currency"' in shop.text
        assert 'name="options.vat"' in shop.text

    async def test_a_new_record_waits_for_the_rest_of_the_form(
        self, per_name: httpx.AsyncClient
    ) -> None:
        page = await per_name.get("/admin/settings/new")

        assert 'name="options~form"' not in page.text
        assert 'hx-post="/admin/settings/document/options?show=form"' in page.text
        assert "from:closest form" in page.text

    async def test_it_is_drawn_again_for_what_the_form_holds(
        self, per_name: httpx.AsyncClient
    ) -> None:
        page = await per_name.get("/admin/settings/new")

        drawn = await per_name.post(
            "/admin/settings/document/options?show=form",
            data={"_csrf": token_in(page), "name": "shop", "options": ""},
        )

        assert drawn.status_code == 200
        assert drawn.text.lstrip().startswith("<fieldset")
        assert 'name="options.currency"' in drawn.text
        assert 'hx-post="/admin/settings/document/options?show=form"' in drawn.text

    async def test_a_new_record_is_checked_against_its_own_schema(
        self, per_name: httpx.AsyncClient, database: Database
    ) -> None:
        page = await per_name.get("/admin/settings/new")
        form = {
            "_csrf": token_in(page),
            "name": "shop",
            "options~form": "1",
            "options.currency": "USD",
            "options.vat": "200",
        }

        refused = await per_name.post("/admin/settings/new", data=form)
        created = await per_name.post(
            "/admin/settings/new", data={**form, "options.vat": "9"}
        )

        assert refused.status_code == 422
        assert "Enter 100 or less." in refused.text
        assert created.status_code == 303
        assert await options_of(database, 3) == {"currency": "USD", "vat": 9.0}

    async def test_a_record_without_a_schema_keeps_the_code_box(
        self, per_name: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            setting = await session.get(Setting, 2)
            assert setting is not None
            setting.name = "anything"
            await session.commit()

        page = await per_name.get("/admin/settings/2/edit")

        assert 'name="options~form"' not in page.text
        assert 'x-data="jsonEditor()"' in page.text


class OverrideView(ModelView[Setting]):
    fields = ["name", JSONField("options", schema=Delivery, partial=True)]


@pytest.fixture
async def overrides(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(OverrideView, database) as client:
        yield client


def held(page: httpx.Response) -> list[str]:
    """The fields a partial form starts with as set, by name."""
    found = re.search(r"picked: (\[.*?\])\}\)'", page.text)
    assert found is not None
    return [item["value"] for item in json.loads(found.group(1))]


def settable_tag(page: httpx.Response, name: str) -> str:
    """The opening tag of the part that holds one field of a partial form."""
    found = re.search(rf'<div [^>]*data-settable="options\.{name}"[^>]*>', page.text)
    assert found is not None
    return found.group(0)


class TestAPartialDocument:
    def test_only_what_is_set_is_read(self) -> None:
        document = Document(Delivery, name="options", partial=True)
        values = document.form_values({"express": True}, "options")

        read = document.read({**values, "options~form": "1"}, "options")

        assert "options.express~set" in values
        assert "options.free_over~set" not in values
        assert values["options.free_over"] == "0"
        assert read == {"express": True}

    def test_a_property_set_is_still_checked(self) -> None:
        document = Document(Delivery, name="options", partial=True)

        with pytest.raises(DocumentError) as raised:
            document.read(
                {
                    "options~form": "1",
                    "options.free_over~set": "1",
                    "options.free_over": "7",
                },
                "options",
            )

        assert raised.value.errors == {
            "options.free_over": "Seven is an unlucky threshold."
        }

    async def test_only_what_is_set_is_drawn(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/1/edit")

        assert held(page) == ["options.carriers", "options.free_over"]
        for name in ("carriers", "free_over"):
            assert "x-cloak" not in settable_tag(page, name)
        for name in ("express", "zones", "fees"):
            assert "x-cloak" in settable_tag(page, name)
        assert 'aria-label="Remove Carriers"' in page.text

    async def test_every_field_waits_in_the_box_in_the_schemas_order(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/1/edit")

        box = re.search(r'<ul id="unset-options".*?</ul>', page.text, flags=re.DOTALL)
        assert box is not None
        offered = re.findall(r'role="option"[^>]*data-value="([^"]+)"', box.group(0))
        assert offered == [
            "options.carriers",
            "options.free_over",
            "options.express",
            "options.zones",
            "options.fees",
        ]
        assert "Orders above this ship free." in box.group(0)
        assert 'aria-label="Add a field"' in page.text

    async def test_every_unset_field_keeps_its_inputs_unsent(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/1/edit")

        for name in ("carriers", "free_over", "express", "zones", "fees"):
            assert f'name="options.{name}~set"' in page.text
        assert page.text.count('<fieldset class="min-w-0 grow"') == 5
        assert re.search(
            r"fieldset[^>]*'!holds\(\"options.express\"\)' disabled>", page.text
        )

    async def test_a_record_with_nothing_set_says_so(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/new")

        assert held(page) == []
        assert re.search(r'<p class="text-muted" x-show="!picked.length" >', page.text)
        assert "Nothing set." in page.text

    async def test_a_field_with_a_mistake_stays_in_view(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/1/edit")

        answer = await overrides.post(
            "/admin/settings/1/edit",
            data={
                "_csrf": token_in(page),
                "name": "delivery",
                "options~form": "1",
                "options.free_over~set": "1",
                "options.free_over": "7",
            },
        )

        assert answer.status_code == 422
        assert held(answer) == ["options.free_over"]
        assert "Seven is an unlucky threshold." in answer.text

    async def test_every_control_has_a_name(self, overrides: httpx.AsyncClient) -> None:
        page = await overrides.get("/admin/settings/1/edit")
        controls = Controls()
        controls.feed(page.text)

        assert controls.unnamed() == []

    async def test_it_saves_back_as_the_keys_set(
        self, overrides: httpx.AsyncClient, database: Database
    ) -> None:
        page = await overrides.get("/admin/settings/1/edit")

        answer = await overrides.post(
            "/admin/settings/1/edit",
            data={
                "_csrf": token_in(page),
                "name": "delivery",
                "options~form": "1",
                "options.carriers~set": "1",
                "options.carriers": ["post"],
                "options.express~set": "1",
            },
        )

        assert answer.status_code == 303
        assert await options_of(database) == {"carriers": ["post"], "express": False}

    async def test_the_record_page_says_what_is_not_set(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/1")

        assert "Not set (3)" in page.text
        unset = re.search(r"<ul [^>]*x-show=\"open\".*?</ul>", page.text, re.DOTALL)
        assert unset is not None
        assert re.findall(r"<li [^>]*>([^<]+)</li>", unset.group(0)) == [
            "Express",
            "Zones",
            "Fees",
        ]
        assert '<dt class="text-[12.5px] text-muted">Carriers</dt>' in page.text
        assert '<dt class="text-[12.5px] text-muted">Express</dt>' not in page.text

    async def test_the_record_page_says_when_nothing_is_set(
        self, overrides: httpx.AsyncClient
    ) -> None:
        page = await overrides.get("/admin/settings/2")

        assert "Nothing set." in page.text
        assert "Not set (5)" in page.text

    def test_the_fields_left_out_are_marked_unset(self) -> None:
        document = Document(Delivery, name="options", partial=True)

        shown = document.shown({"express": True})

        assert [entry.label for entry in shown.unset_entries] == [
            "Carriers",
            "Free over",
            "Zones",
            "Fees",
        ]
        assert not Document(Delivery, name="options").shown({}).unset_entries

    async def test_the_api_keeps_a_partial_document_partial(
        self, overrides: httpx.AsyncClient, database: Database
    ) -> None:
        page = await overrides.get("/admin/settings")

        answer = await overrides.patch(
            "/admin/-/api/settings/1",
            json={"options": {"express": True}},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert answer.status_code == 200
        assert await options_of(database) == {"express": True}


class ReadOnlyView(ModelView[Setting]):
    fields = ["name", JSONField("options", schema=Delivery, read_only=True)]


async def test_a_field_the_user_cannot_edit_is_never_read_back(
    database: Database,
) -> None:
    async with serve(ReadOnlyView, database) as client:
        page = await client.get("/admin/settings/1/edit")
        answer = await client.post(
            "/admin/settings/1/document/options",
            data={"_csrf": token_in(page), **filled()},
        )

    assert 'name="options~form"' not in page.text
    assert answer.status_code == 404
