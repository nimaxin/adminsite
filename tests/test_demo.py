import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import demo.app
from adminsite.audit import AuditEvent, AuditQuery
from adminsite.fields import FileField, ImageField
from demo.app import (
    MEGABYTE,
    VISITOR_COOKIE,
    VISITOR_ID,
    Copy,
    Visitors,
    build_admin,
    build_app,
    sweep_forever,
)
from examples.fields import Showcase
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
    # The first visit only hands out the visitor's cookie.
    page = await client.get("/admin/login", follow_redirects=True)
    response = await client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin", "_csrf": token_from(page.text)},
    )
    assert response.status_code == 303
    return token_from((await client.get("/admin/orders")).text)


def visitors_of(app: FastAPI) -> Visitors:
    visitors = app.state.visitors
    assert isinstance(visitors, Visitors)
    return visitors


def copy_of(app: FastAPI, client: httpx.AsyncClient) -> Copy:
    return visitors_of(app).copies[client.cookies[VISITOR_COOKIE]]


async def count(copy: Copy, table: Any) -> int:
    async with AsyncSession(copy.engine) as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


class TestVisitors:
    async def test_a_new_visitor_gets_a_cookie_and_goes_on(
        self, tmp_path: Path
    ) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            response = await client.get("/admin/orders?status=PAID")

            assert response.status_code == 303
            assert response.headers["location"] == "/admin/orders?status=PAID"
            assert VISITOR_ID.fullmatch(client.cookies[VISITOR_COOKIE])
            # The copy waits for the cookie to come back.
            assert visitors_of(app).copies == {}

    async def test_a_client_that_keeps_no_cookies_makes_no_copy(
        self, tmp_path: Path
    ) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            for _ in range(3):
                assert (await client.get("/admin/")).status_code == 303
                client.cookies.clear()

            assert visitors_of(app).copies == {}
            assert not (tmp_path / "visitors").exists()

    async def test_each_visitor_sees_only_their_own_changes(
        self, tmp_path: Path
    ) -> None:
        app = build_app(tmp_path, SECRET)
        async with (
            app.router.lifespan_context(app),
            client_for(app) as first,
            client_for(app) as second,
        ):
            token = await sign_in(first)
            deleted = await first.post("/admin/orders/1/delete", data={"_csrf": token})
            await sign_in(second)

            assert deleted.status_code == 303
            assert await count(copy_of(app, first), Order) == 23
            assert await count(copy_of(app, second), Order) == 24
            assert (await first.get("/admin/orders/1")).status_code == 404
            assert (await second.get("/admin/orders/1")).status_code == 200

    async def test_a_cookie_that_is_not_an_id_is_replaced(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            client.cookies.set(VISITOR_COOKIE, "../template")
            response = await client.get("/admin/")

            assert response.status_code == 303
            assert VISITOR_ID.fullmatch(response.cookies[VISITOR_COOKIE])
            assert visitors_of(app).copies == {}

    async def test_an_idle_copy_is_thrown_away(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET, idle_seconds=0)
        async with app.router.lifespan_context(app), client_for(app) as client:
            token = await sign_in(client)
            await client.post("/admin/orders/1/delete", data={"_csrf": token})
            folder = copy_of(app, client).folder

            await visitors_of(app).sweep()

            assert visitors_of(app).copies == {}
            assert not folder.exists()
            # Coming back makes a fresh copy, with the order there again.
            assert (await client.get("/admin/orders/1")).status_code == 200
            assert await count(copy_of(app, client), Order) == 24

    async def test_a_busy_copy_is_kept(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET, idle_seconds=0)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await sign_in(client)
            copy_of(app, client).busy += 1

            await visitors_of(app).sweep()

            assert len(visitors_of(app).copies) == 1

    async def test_the_oldest_copy_makes_room(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET, max_visitors=2)
        async with (
            app.router.lifespan_context(app),
            client_for(app) as first,
            client_for(app) as second,
            client_for(app) as third,
        ):
            for client in (first, second, third):
                await client.get("/admin/login", follow_redirects=True)
            names = [
                client.cookies[VISITOR_COOKIE] for client in (first, second, third)
            ]

            assert list(visitors_of(app).copies) == names[1:]
            assert not (tmp_path / "visitors" / names[0]).exists()

    async def test_a_full_demo_says_so(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET, max_visitors=1)
        async with (
            app.router.lifespan_context(app),
            client_for(app) as first,
            client_for(app) as second,
        ):
            await first.get("/admin/login", follow_redirects=True)
            copy_of(app, first).busy += 1
            response = await second.get("/admin/login", follow_redirects=True)

        assert response.status_code == 503
        assert response.headers["retry-after"] == "60"
        assert "The demo is full." in response.text

    async def test_a_restart_starts_from_nothing(self, tmp_path: Path) -> None:
        leftover = tmp_path / "visitors" / ("a" * 22) / "shop.db"
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(b"from the last run")
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app):
            assert not leftover.exists()
            assert (tmp_path / "template" / "shop.db").exists()
            assert (tmp_path / "template" / "uploads" / "samples" / "teal.png").exists()

    async def test_the_sweeps_carry_on_after_a_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        tries = 0

        async def flaky() -> None:
            nonlocal tries
            tries += 1
            if tries == 1:
                raise RuntimeError("the disk is full")
            if tries == 3:
                raise asyncio.CancelledError  # the app shutting down

        visitors = Visitors(tmp_path, SECRET)
        monkeypatch.setattr(visitors, "sweep", flaky)
        monkeypatch.setattr(demo.app, "SWEEP_SECONDS", 0)
        caplog.set_level(logging.INFO, logger="uvicorn.error")

        with pytest.raises(asyncio.CancelledError):
            await sweep_forever(visitors)

        assert tries == 3
        assert "Throwing away idle copies of the demo failed." in caplog.text


class TestDemo:
    async def test_the_sign_in_page_says_how_to_get_in(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            page = await client.get("/admin/login", follow_redirects=True)

        assert "Sign in as admin with the password admin." in page.text
        assert "Only you see what you change" in page.text
        # And the details are already in the boxes.
        assert re.search(r'<input[^>]*name="username"[^>]*value="admin"', page.text)
        assert re.search(r'<input[^>]*name="password"[^>]*value="admin"', page.text)

    async def test_the_front_door_leads_to_the_admin(self, tmp_path: Path) -> None:
        async with client_for(build_app(tmp_path, SECRET)) as client:
            home = await client.get("/")
            health = await client.get("/healthz")

        assert home.headers["location"] == "/admin/"
        assert health.text == "ok"

    async def test_products_are_tagged_with_the_picker(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await sign_in(client)
            products = await client.get("/admin/products")
            form = await client.get("/admin/products/3/edit")

        assert "Handmade" in products.text
        assert "recordPicker(" in form.text
        assert '"label": "Gift idea"' in form.text

    async def test_the_gallery_sits_beside_the_shop(self, tmp_path: Path) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await sign_in(client)
            home = await client.get("/admin/")
            pages = [
                await client.get(path)
                for path in (
                    "/admin/fields",
                    "/admin/fields/1",
                    "/admin/fields/1/edit",
                    "/admin/suppliers",
                )
            ]
            photo = await client.get("/admin/-/files/fields/photo/samples/teal.png")
            showcases = await count(copy_of(app, client), Showcase)

        assert "Field gallery" in home.text
        assert [page.status_code for page in pages] == [200, 200, 200, 200]
        assert "DecimalField, shown in euros." in pages[2].text
        assert photo.status_code == 200
        assert photo.headers["content-type"] == "image/png"
        assert showcases == 14

    def test_strangers_get_smaller_limits(self, tmp_path: Path) -> None:
        admin, _ = build_admin(tmp_path, SECRET)
        views = admin.views
        photo = views.get("products")._fields.field_for("photo")
        uploads = [
            views.get("fields")._fields.field_for(name) for name in ("photo", "manual")
        ]

        assert views.get("customers").import_limit == 200
        assert isinstance(photo, ImageField)
        assert photo.max_size == MEGABYTE
        for upload in uploads:
            assert isinstance(upload, FileField)
            assert upload.max_size == MEGABYTE

    async def test_the_log_keeps_what_was_done_but_not_who_signed_in(
        self, tmp_path: Path
    ) -> None:
        app = build_app(tmp_path, SECRET)
        async with app.router.lifespan_context(app), client_for(app) as client:
            page = await client.get("/admin/login", follow_redirects=True)
            await client.post(
                "/admin/login",
                data={
                    "username": "my real password",
                    "password": "admin",
                    "_csrf": token_from(page.text),
                },
            )
            token = await sign_in(client)
            await client.post("/admin/orders/1/delete", data={"_csrf": token})
            activity = await client.get("/admin/-/activity")
            audit = copy_of(app, client).admin.audit
            assert audit is not None
            entries = await audit.find(AuditQuery(), limit=10)

        assert [entry.event for entry in entries] == [AuditEvent.DELETED]
        assert entries[0].ip is None
        assert "my real password" not in activity.text
