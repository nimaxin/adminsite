import io
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import RefusedError
from adminsite.imports import (
    ImportProblem,
    build_plan,
    import_columns,
    load_plan,
    match_headers,
    read_table,
    save_plan,
)
from adminsite.views.writing import SaveContext
from tests.models import Customer, Order


class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email", "region", "is_active")
    can_import = True


class OrderView(ModelView, model=Order):
    form_fields = ("customer", "status", "total", "created_at")
    can_import = True


class ClosedView(ModelView, model=Customer):
    name = "closed"


def excel(rows: list[list[Any]]) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class TestReading:
    def test_a_csv_with_semicolons_and_a_bom(self) -> None:
        data = "\ufeffname;email\nMia;mia@x.nl\n".encode()

        assert read_table("people.csv", data) == [
            ["name", "email"],
            ["Mia", "mia@x.nl"],
        ]

    def test_a_csv_saved_by_old_excel(self) -> None:
        data = "name,email\nRen\xe9,rene@x.fr\n".encode("cp1252")

        assert read_table("people.csv", data)[1] == ["René", "rene@x.fr"]

    def test_an_excel_file(self) -> None:
        data = excel(
            [["name", "total", "paid"], ["Mia", 12.0, True], [None, None, None]]
        )

        assert read_table("people.xlsx", data) == [
            ["name", "total", "paid"],
            ["Mia", "12", "yes"],
        ]

    def test_other_files_are_refused(self) -> None:
        with pytest.raises(ImportProblem, match="CSV file or an Excel file"):
            read_table("photo.png", b"\x89PNG")

    def test_an_empty_file_is_refused(self) -> None:
        with pytest.raises(ImportProblem, match="empty"):
            read_table("people.csv", b"\n,\n")

    def test_a_broken_excel_file_is_refused(self) -> None:
        with pytest.raises(ImportProblem, match="could not be read"):
            read_table("people.xlsx", b"not a workbook")


class TestColumns:
    def test_the_key_comes_first(self) -> None:
        assert import_columns(CustomerView()) == (
            "id",
            "name",
            "email",
            "region",
            "is_active",
        )

    def test_headers_match_by_name_or_label(self) -> None:
        matched, ignored = match_headers(
            CustomerView(), ["NAME", "Is active", "notes", "name"]
        )

        assert matched == ["name", "is_active", None, None]
        assert ignored == ["notes", "name"]


async def plan_for(database: Database, view: ModelView, csv: str) -> Any:
    async with database.session() as session:
        return await build_plan(view, session, read_table("a.csv", csv.encode()))


class TestThePlan:
    async def test_rows_without_a_key_are_new(self, database: Database) -> None:
        plan = await plan_for(
            database, CustomerView(), "name,email\nMia,mia@x.nl\nTom,tom@x.nl\n"
        )

        assert [row.action for row in plan.rows] == ["create", "create"]
        assert plan.rows[0].values["name"] == "Mia"
        assert plan.rows[0].number == 2

    async def test_rows_with_a_known_key_are_changes(self, database: Database) -> None:
        plan = await plan_for(database, CustomerView(), "id,region\n1,NL\n99,NL\n")

        assert plan.rows[0].action == "update"
        assert plan.rows[0].values == {"region": "NL"}
        assert plan.rows[1].errors == {"id": "No customer has the key 99."}

    async def test_a_new_record_needs_its_required_fields(
        self, database: Database
    ) -> None:
        plan = await plan_for(database, CustomerView(), "name\nMia\n")

        assert plan.rows[0].errors == {"email": "Missing, and a new record needs it."}

    async def test_values_are_checked_like_the_form(self, database: Database) -> None:
        plan = await plan_for(
            database,
            OrderView(),
            'customer,status,total,created_at\n1,Shipped,"1,250.50",2026-09-01 10:00\n'
            "1,lost,abc,soon\n",
        )

        good, bad = plan.rows
        assert good.errors == {}
        assert str(good.values["status"]) == "shipped"
        assert str(good.values["total"]) == "1250.50"
        assert set(bad.errors) == {"status", "total", "created_at"}
        assert plan.count("create") == 1
        assert plan.count("error") == 1

    async def test_yes_or_no_has_to_be_clear(self, database: Database) -> None:
        plan = await plan_for(database, CustomerView(), "id,is_active\n1,No\n2,maybe\n")

        assert plan.rows[0].values == {"is_active": False}
        assert plan.rows[1].errors == {"is_active": "Write yes or no."}

    async def test_none_of_the_columns_match(self, database: Database) -> None:
        with pytest.raises(ImportProblem, match="None of the columns"):
            await plan_for(database, CustomerView(), "colour,size\nred,M\n")


class TestKeptFiles:
    def test_a_kept_file_comes_back_once(self) -> None:
        view = CustomerView()
        token = save_plan(view, "nima", [["name"], ["Mia"]])

        assert load_plan(token, view, "sara") is None
        assert load_plan(token, view, "nima") == [["name"], ["Mia"]]
        assert load_plan(token, view, "nima") is None

    def test_a_token_cannot_reach_other_files(self) -> None:
        assert load_plan("../../secrets", CustomerView(), None) is None


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[CustomerView, ClosedView],
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


async def preview(client: httpx.AsyncClient, csv: str) -> httpx.Response:
    form = await client.get("/admin/customers/import")
    return await client.post(
        "/admin/customers/import",
        data={"_csrf": token_in(form)},
        files={"file": ("people.csv", csv.encode(), "text/csv")},
    )


async def customers(database: Database) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count(Customer.id))) or 0)


class TestImportPages:
    async def test_importing_is_off_unless_switched_on(
        self, client: httpx.AsyncClient
    ) -> None:
        listing = await client.get("/admin/closed")
        form = await client.get("/admin/closed/import")

        assert "/admin/closed/import" not in listing.text
        assert form.status_code == 403

    async def test_the_list_links_to_the_import(
        self, client: httpx.AsyncClient
    ) -> None:
        listing = await client.get("/admin/customers")

        assert 'href="/admin/customers/import"' in listing.text

    async def test_the_form_lists_the_columns(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/customers/import")
        template = await client.get("/admin/customers/import/template")

        assert ">is_active</span>" in form.text
        assert template.text.strip() == "id,name,email,region,is_active"
        assert "attachment" in template.headers["content-disposition"]

    async def test_the_preview_shows_what_will_happen(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await preview(
            client, "id,name,email\n,Mia,mia@x.nl\n1,Lena F.,lena@fischer.de\n,Tom,\n"
        )

        assert page.status_code == 200
        assert "1 new" in page.text
        assert "1 changed" in page.text
        assert "1 with problems" in page.text
        assert "Import 2 rows, skip 1" in page.text
        assert await customers(database) == 4

    async def test_confirming_imports_the_good_rows(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await preview(
            client, "id,name,email\n,Mia,mia@x.nl\n1,Lena F.,lena@fischer.de\n,Tom,\n"
        )
        action = re.search(r'action="(/admin/customers/import/[^"]+)"', page.text)
        assert action is not None

        done = await client.post(
            action.group(1), data={"_csrf": token_in(page)}, follow_redirects=True
        )

        assert "Imported 1 new and changed 1 customers." in done.text
        assert "Skipped 1 rows with problems." in done.text
        assert await customers(database) == 5
        async with database.session() as session:
            lena = await session.get(Customer, 1)
            assert lena is not None
            assert lena.name == "Lena F."

    async def test_a_file_can_be_imported_once(self, client: httpx.AsyncClient) -> None:
        page = await preview(client, "name,email\nMia,mia@x.nl\n")
        action = re.search(r'action="(/admin/customers/import/[^"]+)"', page.text)
        assert action is not None

        await client.post(action.group(1), data={"_csrf": token_in(page)})
        again = await client.post(
            action.group(1), data={"_csrf": token_in(page)}, follow_redirects=True
        )

        assert "This import has expired." in again.text

    async def test_a_bad_file_goes_back_to_the_form(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await preview(client, "colour\nred\n")

        assert page.status_code == 422
        assert "None of the columns match." in page.text

    async def test_the_row_limit_holds(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(CustomerView, "import_limit", 1)
        page = await preview(client, "name,email\nMia,mia@x.nl\nTom,tom@x.nl\n")

        assert "Import at most 1 rows at a time." in page.text

    async def test_a_hook_that_refuses_a_row_is_reported(
        self, database: Database
    ) -> None:
        class Picky(CustomerView):
            name = "picky"

            async def before_save(self, context: SaveContext) -> None:
                if context.values.get("name") == "Tom":
                    raise RefusedError("No Toms.")

        admin = Admin(
            database, title="Shop", views=[Picky], secret_key="for-the-session"
        )
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            form = await client.get("/admin/picky/import")
            page = await client.post(
                "/admin/picky/import",
                data={"_csrf": token_in(form)},
                files={"file": ("a.csv", b"name,email\nMia,mia@x.nl\nTom,tom@x.nl\n")},
            )
            action = re.search(r'action="(/admin/picky/import/[^"]+)"', page.text)
            assert action is not None
            done = await client.post(
                action.group(1), data={"_csrf": token_in(page)}, follow_redirects=True
            )

        assert "Imported 1 new" in done.text
        assert "Refused 1: row 3: No Toms." in done.text
