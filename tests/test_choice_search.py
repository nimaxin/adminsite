"""A choice filter with many options is a picker, which narrows them as you type."""

import re
from collections.abc import Sequence

import httpx
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite._http.listing import SEARCHABLE_OVER, FilterPanel
from adminsite.database import Database
from adminsite.filters import (
    BooleanFilter,
    ChoiceFilter,
    FilterOption,
    FilterValue,
    TextFilter,
)
from tests.models import Customer
from tests.test_accessibility import Controls

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
# What the filters drawer sends as it opens, asking for the options' counts.
DRAWER = {"HX-Request": "true", "HX-Trigger": "filters"}


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


class TestWhatThePickerHolds:
    def test_what_is_picked_in_the_order_given(self) -> None:
        panel = FilterPanel(
            ChoiceFilter("region"),
            [FilterOption("NL", "Netherlands"), FilterOption("SE", "Sweden")],
            FilterValue("region", ("SE", "XX", "NL")),
        )

        assert panel.choice_picker.picked_pairs == [
            {"value": "SE", "label": "Sweden"},
            {"value": "NL", "label": "Netherlands"},
        ]


class OneRegion(ChoiceFilter):
    """A choice filter that takes one value at a time."""

    multiple = False


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def view_with(
    choices: Sequence[tuple[str, str]],
    kind: type[ChoiceFilter] = ChoiceFilter,
    *,
    counted: bool = False,
) -> type[ModelView[Customer]]:
    class CustomerView(ModelView[Customer]):
        fields = ["name", "region"]
        list_filters = [kind("region", choices=choices, show_counts=counted)]

    return CustomerView


async def list_page(
    database: Database,
    view: type[ModelView[Customer]],
    query: str = "",
    headers: dict[str, str] | None = None,
) -> str:
    async with serve(Admin(database, views=[view])) as client:
        page = await client.get(f"/admin/customers{query}", headers=headers)
    return page.text


def panel_of(page: str) -> str:
    """The region filter's part of the filters drawer, from start to end."""
    found = re.search(r'<fieldset id="filter-region".*?</fieldset>', page, re.DOTALL)
    assert found is not None
    return found.group(0)


class TestThePanel:
    async def test_a_long_list_is_a_picker(self, database: Database) -> None:
        panel = panel_of(await list_page(database, view_with(COUNTRIES)))

        assert "recordPicker({multiple: true, ordered: false, local: true" in panel
        assert '<input type="hidden" name="region" :value="item.value">' in panel
        assert panel.count('role="option"') == 250
        assert 'type="checkbox"' not in panel

    async def test_a_short_list_is_boxes_to_tick(self, database: Database) -> None:
        panel = panel_of(await list_page(database, view_with(FEW)))

        assert "recordPicker" not in panel
        assert panel.count('<input type="checkbox" name="region"') == 5

    async def test_what_is_typed_is_never_sent(self, database: Database) -> None:
        panel = panel_of(await list_page(database, view_with(COUNTRIES)))
        box = re.search(r'<input type="text" id="field-filter-region"[^>]*>', panel)

        assert box is not None
        assert "name=" not in box.group(0)
        assert 'aria-label="Region"' in box.group(0)

    async def test_what_is_picked_is_held(self, database: Database) -> None:
        panel = panel_of(
            await list_page(database, view_with(COUNTRIES), "?region=SE&region=EG")
        )

        assert (
            'picked: [{"label": "Sweden", "value": "SE"}, '
            '{"label": "Egypt", "value": "EG"}]'
        ) in panel

    async def test_a_filter_of_one_value_holds_one(self, database: Database) -> None:
        panel = panel_of(await list_page(database, view_with(COUNTRIES, OneRegion)))

        assert "recordPicker({multiple: false," in panel
        assert "oneChoice" not in panel

    async def test_every_control_is_named(self, database: Database) -> None:
        controls = Controls()
        controls.feed(await list_page(database, view_with(COUNTRIES)))

        assert controls.unnamed() == []


class TestItsCounts:
    async def test_each_option_has_a_place_for_its_count(
        self, database: Database
    ) -> None:
        view = view_with(COUNTRIES, counted=True)

        panel = panel_of(await list_page(database, view))
        answer = await list_page(database, view, headers=DRAWER)

        places = [str(number) for number in range(1, len(COUNTRIES) + 1)]
        assert re.findall(r'id="filter-region-count-(\d+)"', panel) == places
        assert re.findall(r'id="filter-region-count-(\d+)"', answer) == places

    async def test_none_where_the_filter_shows_none(self, database: Database) -> None:
        panel = panel_of(await list_page(database, view_with(COUNTRIES)))

        assert "-count-" not in panel
