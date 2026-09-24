import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

import demo.app
from adminsite.audit import audit_table
from adminsite.fields import ImageField
from adminsite.saved_views import saved_view_table
from demo.app import (
    MEGABYTE,
    build_app,
    reset,
    reset_every_hour,
    seconds_until_reset,
)
from examples.shop import Order

SECRET = "a-secret-for-the-tests"


def client_for(app: FastAPI) -> httpx.AsyncClient:
    # The session cookie is only sent back over HTTPS, as on the real demo.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    )


def token_from(body: str) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', body)
    assert found is not None
    return found.group(1)


async def sign_in(client: httpx.AsyncClient) -> str:
    """Sign in as the demo says to, and return the new session's token."""
    page = await client.get("/admin/login")
    response = await client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin", "_csrf": token_from(page.text)},
    )
    assert response.status_code == 303
    return token_from((await client.get("/admin/orders")).text)


async def count(app: FastAPI, table: Any) -> int:
    async with AsyncSession(app.state.engine) as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


class TestReset:
    async def test_it_undoes_what_visitors_did(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            token = await sign_in(client)
            deleted = await client.post("/admin/orders/1/delete", data={"_csrf": token})
            saved = await client.post(
                "/admin/orders/saved-views",
                data={"name": "Paid", "query": "status=PAID", "_csrf": token},
            )
            leftover = tmp_path / "uploads" / "2026-09" / "photo.png"
            leftover.parent.mkdir(parents=True)
            leftover.write_bytes(b"not really a photo")

            assert deleted.status_code == 303
            assert saved.status_code == 303
            assert await count(app, Order) == 23
            assert await count(app, audit_table) > 0
            assert await count(app, saved_view_table) == 1

            await reset(app.state.engine, app.state.uploads)

            assert await count(app, Order) == 24
            assert await count(app, audit_table) == 0
            assert await count(app, saved_view_table) == 0
            assert not (tmp_path / "uploads").exists()
            # The keys start again from one, so a shared link to a record
            # keeps working after the reset.
            assert (await client.get("/admin/orders/1")).status_code == 200

    async def test_the_hourly_loop_carries_on_after_a_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        tries = 0

        async def flaky(engine: AsyncEngine, uploads: Path) -> None:
            nonlocal tries
            tries += 1
            if tries == 1:
                raise RuntimeError("the disk is full")
            if tries == 3:
                raise asyncio.CancelledError  # the app shutting down

        monkeypatch.setattr(demo.app, "reset", flaky)
        monkeypatch.setattr(demo.app, "seconds_until_reset", lambda now: 0)
        caplog.set_level(logging.INFO, logger="uvicorn.error")

        with pytest.raises(asyncio.CancelledError):
            await reset_every_hour(create_async_engine("sqlite+aiosqlite://"), Path())

        assert tries == 3
        assert "Resetting the demo failed." in caplog.text
        assert "The demo is back as it started." in caplog.text

    def test_it_falls_at_the_start_of_an_hour(self) -> None:
        assert seconds_until_reset(7200.0) == 3600
        assert seconds_until_reset(7200.5) == 3599.5
        assert seconds_until_reset(10799.0) == 1


class TestDemo:
    async def test_the_sign_in_page_says_how_to_get_in(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            page = await client.get("/admin/login")

        assert "Sign in as admin with the password admin." in page.text

    async def test_the_front_door_leads_to_the_admin(self, tmp_path: Path) -> None:
        async with client_for(build_app(tmp_path, SECRET)) as client:
            home = await client.get("/")
            health = await client.get("/healthz")

        assert home.headers["location"] == "/admin/"
        assert health.text == "ok"

    def test_strangers_get_smaller_limits(self, tmp_path: Path) -> None:
        views = build_app(tmp_path, SECRET).state.admin.views
        photo = views.get("products").field_for("photo")

        assert views.get("customers").import_limit == 200
        assert isinstance(photo, ImageField)
        assert photo.max_size == MEGABYTE
