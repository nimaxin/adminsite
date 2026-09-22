import json
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.backends.sqlalchemy import Database
from adminsite.i18n import (
    activate,
    direction,
    gettext,
    negotiate,
    shipped_languages,
)
from tests.messages import PACKAGE, missing
from tests.models import Customer, Order

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def english_afterwards() -> Iterator[None]:
    yield
    activate("en")


class TestGettext:
    def test_english_is_the_text_itself(self) -> None:
        activate("en")

        assert gettext("Save") == "Save"
        assert gettext("Page {number}", number=3) == "Page 3"

    def test_persian_comes_from_the_catalog(self) -> None:
        activate("fa")

        assert gettext("Save") == "ذخیره"
        assert gettext("Page {number}", number=3) == "صفحهٔ 3"

    def test_text_without_a_translation_stays_english(self) -> None:
        activate("fa")

        assert gettext("Something nobody translated") == "Something nobody translated"

    def test_a_language_without_a_catalog_is_english(self) -> None:
        activate("xx")

        assert gettext("Save") == "Save"

    def test_a_projects_own_translations_win(self) -> None:
        activate("fa", {"fa": {"Save": "ثبت"}, "de": {"Save": "Speichern"}})

        assert gettext("Save") == "ثبت"
        assert gettext("Cancel") == "انصراف"

    def test_right_to_left_languages(self) -> None:
        assert direction("fa") == "rtl"
        assert direction("ar-EG") == "rtl"
        assert direction("en") == "ltr"

    def test_shipped_languages(self) -> None:
        assert {"en", "fa"} <= set(shipped_languages())


class TestNegotiation:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("fa-IR,fa;q=0.9,en;q=0.8", "fa"),
            ("de-DE,en;q=0.5", "en"),
            ("en;q=0.3,fa;q=0.7", "fa"),
            ("de", None),
            ("", None),
            ("fa;q=abc,en", "en"),
        ],
    )
    def test_the_best_offered_language(self, header: str, expected: str | None) -> None:
        assert negotiate(header, ["en", "fa"]) == expected


class TestTheCatalog:
    def test_persian_translates_everything(self) -> None:
        assert missing("fa") == []

    def test_placeholders_survive_translation(self) -> None:
        catalog = json.loads(
            (PACKAGE / "locales" / "fa.json").read_text(encoding="utf-8")
        )
        for english, persian in catalog.items():
            assert set(PLACEHOLDER.findall(english)) == set(
                PLACEHOLDER.findall(persian)
            ), english


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    search_fields = ("customer.name",)


class LockedView(ModelView, model=Customer):
    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        return action != Permission.CREATE


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def persian(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="فروشگاه",
        views=[OrderView, LockedView],
        language="fa",
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


class TestAPersianAdmin:
    async def test_the_page_is_mirrored(self, persian: httpx.AsyncClient) -> None:
        page = await persian.get("/admin/orders")

        assert '<html lang="fa" dir="rtl">' in page.text
        assert "ویرایش" in page.text
        assert "جست‌وجو در orders" in page.text
        assert "1 تا 7 از 7" in page.text

    async def test_messages_are_translated(self, persian: httpx.AsyncClient) -> None:
        form = await persian.get("/admin/orders/1/edit")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        page = await persian.post(
            "/admin/orders/1/edit",
            data={"_csrf": token.group(1), "total": "abc", "customer": "1"},
        )

        assert "یک مبلغ وارد کنید، مثلاً 12.50." in page.text

    async def test_refusals_are_translated(self, persian: httpx.AsyncClient) -> None:
        page = await persian.get("/admin/customers/new")

        assert page.status_code == 403
        assert "دسترسی ندارید" in page.text
        assert "اجازهٔ افزودن Customers را ندارید." in page.text

    async def test_a_single_language_admin_has_no_menu(
        self, persian: httpx.AsyncClient
    ) -> None:
        page = await persian.get("/admin/")

        assert 'name="language"' not in page.text


@pytest.fixture
async def both(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView],
        languages=["fa"],
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


class TestSwitchingLanguages:
    async def test_the_browser_language_is_used(self, both: httpx.AsyncClient) -> None:
        english = await both.get("/admin/")
        persian = await both.get("/admin/", headers={"Accept-Language": "fa-IR,fa"})

        assert '<html lang="en" dir="ltr">' in english.text
        assert '<html lang="fa" dir="rtl">' in persian.text

    async def test_the_menu_switches_and_remembers(
        self, both: httpx.AsyncClient
    ) -> None:
        home = await both.get("/admin/")
        token = re.search(r'name="_csrf" value="([^"]+)"', home.text)
        assert token is not None
        assert '<option value="fa" lang="fa" >فارسی</option>' in home.text

        answer = await both.post(
            "/admin/-/language",
            data={"_csrf": token.group(1), "language": "fa", "next": "/admin/orders"},
        )
        after = await both.get("/admin/orders")

        assert answer.status_code == 303
        assert answer.headers["location"] == "/admin/orders"
        assert '<html lang="fa" dir="rtl">' in after.text

    async def test_the_menu_only_goes_back_inside_the_site(
        self, both: httpx.AsyncClient
    ) -> None:
        home = await both.get("/admin/")
        token = re.search(r'name="_csrf" value="([^"]+)"', home.text)
        assert token is not None

        answer = await both.post(
            "/admin/-/language",
            data={
                "_csrf": token.group(1),
                "language": "fa",
                "next": "//evil.example/steal",
            },
        )

        assert answer.headers["location"] == "/admin/"

    async def test_a_language_not_offered_is_ignored(
        self, both: httpx.AsyncClient
    ) -> None:
        home = await both.get("/admin/")
        token = re.search(r'name="_csrf" value="([^"]+)"', home.text)
        assert token is not None

        answer = await both.post(
            "/admin/-/language",
            data={"_csrf": token.group(1), "language": "de", "next": "/admin/"},
        )

        assert "adminsite_language" not in answer.headers.get("set-cookie", "")
