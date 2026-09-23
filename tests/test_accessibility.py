import re
from collections.abc import AsyncIterator
from html.parser import HTMLParser

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Inline, ModelView
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, Product


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total")
    list_filter = ("status", "total", "created_at")
    search_fields = ("customer.name",)
    form_fields = ("customer", "status", "total", "note", "created_at")
    inlines = (Inline("items", fields=("product", "quantity", "unit_price")),)
    page_size = 3
    can_import = True

    @action("Remove")
    async def remove(self, selection: Selection) -> str:
        return f"{await selection.delete()} removed."


class CustomerView(ModelView, model=Customer):
    pass


class ProductView(ModelView, model=Product):
    pass


class Controls(HTMLParser):
    """Collects the form controls of a page and whatever names them."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[dict[str, str | None]] = []
        self.label_for: set[str] = set()
        self.label_depth = 0
        self.button: dict[str, str | None] | None = None
        self.button_text = ""
        self.template_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "template":
            self.template_depth += 1
        if tag == "label":
            self.label_depth += 1
            if found.get("for"):
                self.label_for.add(str(found["for"]))
        if tag in ("input", "select", "textarea"):
            if found.get("type") in ("hidden", "radio") or "hidden" in found:
                return
            found["_wrapped"] = "yes" if self.label_depth else None
            found["_tag"] = tag
            self.controls.append(found)
        if tag == "button":
            self.button = found
            self.button_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "template":
            self.template_depth -= 1
        if tag == "label":
            self.label_depth -= 1
        if tag == "button" and self.button is not None:
            self.button["_text"] = self.button_text.strip()
            self.button["_tag"] = "button"
            self.controls.append(self.button)
            self.button = None

    def handle_data(self, data: str) -> None:
        if self.button is not None:
            self.button_text += data

    def unnamed(self) -> list[dict[str, str | None]]:
        """The controls nothing gives a name."""
        return [
            control
            for control in self.controls
            if not (
                control.get("aria-label")
                or control.get("aria-labelledby")
                or control.get("_wrapped")
                or control.get("_text")
                or (control.get("id") and control["id"] in self.label_for)
                or (control.get("type") in ("submit",) and control.get("value"))
            )
        ]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView, CustomerView],
        secret_key="for-the-session",
        saved_views=False,
        languages=["fa"],
    )
    async with serve(admin) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/admin/",
        "/admin/orders",
        "/admin/orders/1",
        "/admin/orders/1/edit",
        "/admin/orders/new",
        "/admin/orders/import",
    ],
)
async def test_every_control_has_a_name(client: httpx.AsyncClient, path: str) -> None:
    page = await client.get(path)
    controls = Controls()
    controls.feed(page.text)

    assert page.status_code == 200
    assert controls.unnamed() == []


async def test_the_login_labels_its_inputs(database: Database) -> None:
    admin = Admin(
        database,
        auth=PasswordAuth({"nima": hash_password("letmein")}),
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        page = await client.get("/admin/login")
    controls = Controls()
    controls.feed(page.text)

    assert controls.unnamed() == []
    assert '<html lang="en" dir="ltr">' in page.text


class TestThePage:
    async def test_a_skip_link_leads_to_the_content(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert page.text.index('href="#content"') < page.text.index("<aside")
        assert '<main id="content" tabindex="-1"' in page.text

    async def test_the_sidebar_is_named_and_marks_where_you_are(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert re.search(r'<nav [^>]*aria-label="Main"', page.text)
        assert re.search(r'href="/admin/orders" aria-current="page"', page.text)
        assert not re.search(r'href="/admin/customers" aria-current', page.text)


class TestTheList:
    async def test_the_table_is_captioned_and_the_sort_announced(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?sort=-total")

        assert '<caption class="sr-only">Orders</caption>' in page.text
        assert re.search(r'aria-sort="descending"', page.text)
        assert page.text.count("aria-sort=") == 1

    async def test_the_pager_is_navigation(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert re.search(r'<nav [^>]*aria-label="Pages"', page.text)
        assert re.search(r'aria-disabled="true" tabindex="-1"\s+href=', page.text)

    async def test_rows_are_chosen_by_name(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert 'aria-label="Select Order #1"' in page.text

    async def test_new_results_are_announced(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert '<p id="records-status" class="sr-only" aria-live="polite">' in page.text
        assert '<span class="tabular-nums" data-count>' in page.text

    async def test_each_filter_says_whether_it_is_open(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert re.search(
            r'aria-expanded="false"\s+aria-controls="filter-status"', page.text
        )
        assert 'id="filter-status"' in page.text


class TestTheForm:
    async def test_a_field_is_named_by_its_label(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1/edit")

        assert 'id="field-total-label"' in page.text
        assert 'aria-labelledby="field-total-label"' in page.text

    async def test_an_error_is_tied_to_its_field(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/orders/1/edit")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        page = await client.post(
            "/admin/orders/1/edit",
            data={"_csrf": token.group(1), "customer": "1", "total": "abc"},
        )

        assert page.status_code == 422
        assert re.search(
            r'aria-describedby="field-total-note" aria-invalid="true"', page.text
        )
        assert 'id="field-total-note"' in page.text

    async def test_inline_cells_are_named_by_their_column(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1/edit")

        assert 'aria-label="Quantity"' in page.text


class TestMessages:
    async def test_a_problem_stays_until_it_is_closed(self, database: Database) -> None:
        admin = Admin(database, views=[ProductView], secret_key="for-the-session")
        async with serve(admin) as client:
            form = await client.get("/admin/products")
            token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
            assert token is not None
            # Order lines still point at this product, so the delete is refused.
            await client.post(
                "/admin/products/1/delete", data={"_csrf": token.group(1)}
            )
            page = await client.get("/admin/products")

        problem = page.text.split('role="alert"', 1)[1].split("</div>", 1)[0]
        assert "cannot be deleted" in problem
        assert "setTimeout" not in problem
        assert 'aria-label="Close"' in problem
