"""A long choice filter narrows its options as you type."""

import re
from collections.abc import Sequence

import httpx
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite._http.listing import SEARCHABLE_OVER, FilterPanel
from adminsite.database import Database
from adminsite.filters import BooleanFilter, ChoiceFilter, FilterOption, TextFilter
from tests.models import Customer

# Countries enough to need a search, Egypt among them.
COUNTRIES = [
    *((f"C{number:03}", f"Country {number}") for number in range(247)),
    ("EG", "Egypt"),
    ("NL", "Netherlands"),
    ("SE", "Sweden"),
]
FEW = [
    ("NL", "Netherlands"),
    ("DE", "Germany"),
    ("SE", "Sweden"),
    ("BE", "Belgium"),
    ("FR", "France"),
]


def options(count: int) -> Sequence[FilterOption]:
    return [FilterOption(f"V{number}", f"Value {number}") for number in range(count)]


class TestWhenToSearch:
    def test_more_options_than_the_limit(self) -> None:
        many = FilterPanel(ChoiceFilter("region"), options(SEARCHABLE_OVER + 1))
        enough = FilterPanel(ChoiceFilter("region"), options(SEARCHABLE_OVER))

        assert many.searchable is True
        assert enough.searchable is False

    def test_only_a_list_of_choices(self) -> None:
        typed = FilterPanel(TextFilter("note"), options(50))
        yes_or_no = FilterPanel(BooleanFilter("is_active"), options(2))

        assert typed.searchable is False
        assert yes_or_no.searchable is False


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def view_with(choices: Sequence[tuple[str, str]]) -> type[ModelView[Customer]]:
    class CustomerView(ModelView[Customer]):
        fields = ["name", "region"]
        list_filters = [ChoiceFilter("region", choices=choices, show_counts=False)]

    return CustomerView


async def list_page(
    database: Database, choices: Sequence[tuple[str, str]], query: str = ""
) -> str:
    async with serve(Admin(database, views=[view_with(choices)])) as client:
        page = await client.get(f"/admin/customers{query}")
    return page.text


def panel_of(page: str) -> str:
    """The region filter's panel, from its form to the end of the form."""
    found = re.search(r'<form id="filter-region".*?</form>', page, re.DOTALL)
    assert found is not None
    return found.group(0)


class TestThePanel:
    async def test_a_long_list_has_a_search_box(self, database: Database) -> None:
        panel = panel_of(await list_page(database, COUNTRIES))

        assert 'x-data="choiceSearch()"' in panel
        assert 'aria-label="Search region"' in panel
        assert 'data-label="Egypt" x-show="shows($el)"' in panel
        assert panel.count('x-show="shows($el)"') == 250

    async def test_a_short_list_has_none(self, database: Database) -> None:
        panel = panel_of(await list_page(database, FEW))

        assert "choiceSearch" not in panel
        assert "shows(" not in panel
        assert panel.count('name="region"') == 5

    async def test_what_is_typed_is_never_sent(self, database: Database) -> None:
        panel = panel_of(await list_page(database, COUNTRIES))
        box = re.search(r'<input type="search"[^>]*>', panel)

        assert box is not None
        assert "name=" not in box.group(0)

    async def test_a_ticked_option_is_drawn_ticked_for_the_search_to_keep(
        self, database: Database
    ) -> None:
        panel = panel_of(await list_page(database, COUNTRIES, "?region=EG"))
        egypt = re.search(
            r'<input type="checkbox" name="region" value="EG"[^>]*>', panel
        )

        assert egypt is not None
        assert "checked" in egypt.group(0)
