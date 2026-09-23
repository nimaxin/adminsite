import dataclasses
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.actions import Selection, action
from adminsite.backends.sqlalchemy import Database
from adminsite.fields import BooleanField, ChoiceField
from tests.models import Order

CATEGORIES = (("paper", "Paper"), ("card", "Card"), ("film", "Film"))

seen: dict[str, Any] = {}


class OrderView(ModelView, model=Order):
    list_display = ("id", "status")
    ordering = ("id",)

    @action(
        "Download",
        inputs=(
            ChoiceField(
                "categories",
                choices=CATEGORIES,
                multiple=True,
                default=("paper", "film"),
            ),
            BooleanField("with_totals", label="With totals", default=True),
            ChoiceField("shape", choices=(("csv", "CSV"), ("pdf", "PDF"))),
        ),
    )
    async def download(self, selection: Selection, **values: Any) -> str:
        seen.clear()
        seen.update(values)
        return "Downloaded."


class PerRequestView(ModelView, model=Order):
    """The choices are worked out when the page is drawn."""

    name = "live_orders"
    list_display = ("id",)

    @action("Move", inputs=(ChoiceField("target", choices=()),))
    async def move(self, selection: Selection, **values: Any) -> str:
        seen.clear()
        seen.update(values)
        return "Moved."

    def get_actions(self, request: Any = None) -> tuple[Any, ...]:
        found = super().get_actions(request)
        choices = (("north", "North"), ("south", "South"))
        return tuple(
            dataclasses.replace(
                item, inputs=(ChoiceField("target", choices=choices, default="south"),)
            )
            for item in found
        )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView, PerRequestView],
        secret_key="for-the-session",
    )
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


def dialog_for(page: httpx.Response, name: str) -> str:
    return page.text.split(f'id="action-{name}"', 1)[1].split("</dialog>", 1)[0]


def input_for(dialog: str, name: str) -> str:
    """The one input tag carrying this name."""
    found = re.search(rf'<input[^>]*name="{name}"[^>]*>', dialog)
    assert found is not None, dialog
    return found.group(0)


class TestHowTheDialogOpens:
    async def test_a_switch_can_start_on(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        switch = input_for(dialog_for(page, "download"), "with_totals")

        assert "checked" in switch

    async def test_a_select_can_hold_several(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        dialog = dialog_for(page, "download")
        assert 'name="categories"' in dialog
        assert "multiple" in dialog

    async def test_the_options_it_starts_with_are_chosen(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        dialog = dialog_for(page, "download")
        chosen = re.findall(r'<option value="([^"]+)" selected>', dialog)
        assert chosen == ["paper", "film"]

    async def test_a_field_with_no_default_starts_empty(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        shape = dialog_for(page, "download").split('name="shape"', 1)[1]
        assert "selected" not in shape.split("</select>", 1)[0]


class TestWhatComesBack:
    async def test_every_option_chosen_is_read(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        answer = await client.post(
            "/admin/orders/action/download",
            data={
                "_csrf": token_in(page),
                "keys": ["1", "2"],
                "categories": ["paper", "card"],
                "with_totals": "true",
                "shape": "csv",
            },
        )

        assert answer.status_code == 303
        assert seen["categories"] == ["paper", "card"]
        assert seen["with_totals"] is True
        assert seen["shape"] == "csv"

    async def test_nothing_chosen_reads_as_nothing(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        await client.post(
            "/admin/orders/action/download",
            data={"_csrf": token_in(page), "keys": ["1"], "shape": "csv"},
        )

        assert seen["categories"] == []
        assert seen["with_totals"] is False

    async def test_an_option_nobody_offered_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")
        seen.clear()

        answer = await client.post(
            "/admin/orders/action/download",
            data={
                "_csrf": token_in(page),
                "keys": ["1"],
                "categories": ["glass"],
                "shape": "csv",
            },
            follow_redirects=True,
        )

        assert seen == {}
        assert "Choose one of the listed options." in answer.text


class TestChoicesPerRequest:
    async def test_the_dialog_shows_them(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/live_orders")

        dialog = dialog_for(page, "move")
        assert "North" in dialog
        assert '<option value="south" selected>' in dialog

    async def test_the_run_accepts_them(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/live_orders")

        answer = await client.post(
            "/admin/live_orders/action/move",
            data={"_csrf": token_in(page), "keys": ["1"], "target": "north"},
        )

        assert answer.status_code == 303
        assert seen["target"] == "north"
