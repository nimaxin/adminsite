import dataclasses
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Action, Selection, action
from adminsite.actions.base import action_of
from adminsite.database import Database
from tests.models import Order

PAID = '<svg data-icon="paid" viewBox="0 0 16 16"><path d="M2 8h12"/></svg>'
SYNC = '<svg data-icon="sync" viewBox="0 0 16 16"><path d="M8 2v12"/></svg>'
NOTE = '<svg data-icon="note" viewBox="0 0 16 16"><path d="M2 2h12"/></svg>'
SWAPPED = '<svg data-icon="swapped" viewBox="0 0 16 16"><path d="M2 2h4"/></svg>'
BY_HAND = '<svg data-icon="by-hand" viewBox="0 0 16 16"><path d="M2 2h8"/></svg>'


class OrderView(ModelView[Order]):
    fields = ["id", "status", "total"]

    @action("Mark as paid", on="record", icon=PAID)
    async def mark_paid(self, record: Order) -> str:
        return "Marked as paid."

    @action("Cancel", on="record", dangerous=True, icon="icons/cancel.svg")
    async def cancel(self, record: Order) -> str:
        return "Cancelled."

    @action("Archive", on="record")
    async def archive(self, record: Order) -> str:
        return "Archived."

    @action("Sync", on="view", permission=Permission.VIEW, icon=SYNC)
    async def sync(self) -> str:
        return "Synced."

    @action("Note them all", icon=NOTE)
    async def note(self, selection: Selection[Order]) -> str:
        return "Noted."


class ChangedView(OrderView):
    """Gives an action its icon in get_actions, and adds one built by hand."""

    name = "changed"

    async def by_hand(self, record: Order) -> str:
        return "Done by hand."

    def get_actions(self, request: Request) -> tuple[Any, ...]:
        return (
            *(
                dataclasses.replace(item, icon=SWAPPED)
                if item.name == "archive"
                else item
                for item in super().get_actions(request)
            ),
            Action(
                name="by_hand",
                label="By hand",
                method="by_hand",
                on="record",
                icon=BY_HAND,
            ),
        )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[OrderView, ChangedView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def buttons(page: httpx.Response, opens: str) -> list[str]:
    """Every button whose attributes hold `opens`, from its tag to its end."""
    pattern = rf"<button[^>]*{re.escape(opens)}[^>]*>.*?</button>"
    found = re.findall(pattern, page.text, flags=re.DOTALL)
    assert found, f"No button with {opens} on the page."
    return found


def icon_before_label(button: str, icon: str, label: str) -> bool:
    """Whether the button draws this icon, and draws it before its label."""
    return icon in button and button.index(icon) < button.index(label)


class TestWhereTheIconIsDrawn:
    async def test_in_a_rows_menu(self, client: httpx.AsyncClient) -> None:
        listed = await client.get("/admin/orders")

        (row,) = buttons(listed, 'runRecordAction("mark_paid", "1")')
        assert icon_before_label(row, 'data-icon="paid"', "Mark as paid")

    async def test_on_the_record_page_and_its_phone_menu(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1")

        found = buttons(page, 'runRecordAction("mark_paid", "1")')
        assert len(found) == 2
        assert all(
            icon_before_label(button, 'data-icon="paid"', "Mark as paid")
            for button in found
        )

    async def test_above_the_list_and_in_its_phone_menu(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        found = buttons(listed, 'runRecordAction("sync", "")')
        assert len(found) == 2
        assert all(
            icon_before_label(button, 'data-icon="sync"', "Sync") for button in found
        )

    async def test_in_the_bar_over_the_ticked_rows(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        (bar,) = buttons(listed, 'formaction="/admin/orders/action/note"')
        assert icon_before_label(bar, 'data-icon="note"', "Note them all")

    async def test_not_in_the_dialog_that_asks_first(self, database: Database) -> None:
        class Asking(ModelView[Order]):
            @action("Ship", on="record", confirm="Ship it?", icon=PAID)
            async def ship(self, record: Order) -> str:
                return "Shipped."

        admin = Admin(database, views=[Asking])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            listed = await client.get("/admin/orders")

        dialog = re.search(
            r'<dialog id="action-ship".*?</dialog>', listed.text, flags=re.DOTALL
        )
        assert dialog is not None
        assert 'data-icon="paid"' not in dialog.group(0)


class TestWhatTheIconIs:
    async def test_a_picture_is_served_from_the_admin(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        (row,) = buttons(listed, 'runRecordAction("cancel", "1")')
        assert icon_before_label(
            row, '<img src="/admin/icons/cancel.svg" alt="">', "Cancel"
        )

    async def test_it_is_hidden_from_screen_readers(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        (row,) = buttons(listed, 'runRecordAction("mark_paid", "1")')
        assert re.search(r'<span [^>]*aria-hidden="true">\s*<svg data-icon="paid"', row)

    async def test_grey_beside_a_label_and_red_beside_a_dangerous_one(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        (plain,) = buttons(listed, 'runRecordAction("mark_paid", "1")')
        (dangerous,) = buttons(listed, 'runRecordAction("cancel", "1")')
        assert "size-3.5 text-muted" in plain
        assert "text-muted" not in dangerous
        assert 'class="text-error"' in dangerous

    async def test_an_action_without_one_draws_its_label_alone(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        (row,) = buttons(listed, 'runRecordAction("archive", "1")')
        inner = row.split(">", 1)[1].rsplit("</button>", 1)[0]
        assert inner.strip() == "Archive"


class TestAnIconSetInGetActions:
    async def test_a_copy_made_with_replace_carries_it(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/changed")

        (row,) = buttons(listed, 'runRecordAction("archive", "1")')
        assert icon_before_label(row, 'data-icon="swapped"', "Archive")

    async def test_an_action_built_by_hand_carries_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/changed/1")

        found = buttons(page, 'runRecordAction("by_hand", "1")')
        assert all(
            icon_before_label(button, 'data-icon="by-hand"', "By hand")
            for button in found
        )

    def test_the_decorator_keeps_it_on_the_action(self) -> None:
        marked = action_of(OrderView.mark_paid)

        assert marked is not None
        assert marked.icon == PAID
