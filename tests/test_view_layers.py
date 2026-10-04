"""The parts of a view use only the parts below them.

ModelView builds its parts in __init__, from the bottom row up, and hands
each the parts it uses. A module under views/ imports, when it runs, only
modules of the rows below its own, never one beside or above it. Under
TYPE_CHECKING it may name anything, so a part can annotate the view that
holds it. AGENTS.md shows the same rows.
"""

from pathlib import Path

import pytest

from tests.test_web_layer import imported

VIEWS = Path(__file__).parent.parent / "src" / "adminsite" / "views"
PACKAGE = "adminsite.views"
# From the bottom up. A module in a row imports only modules of the rows
# before it.
LAYERS = [
    ["_checks", "_linked_names", "layout", "inlines", "contexts"],
    ["_settings"],
    ["_fields", "_inline_views"],
    ["_pages"],
    ["_reader", "_forms", "_audit"],
    ["_links"],
    ["_saver", "_actions"],
    ["model_view"],
    ["registry", "_picker"],
]
ROW = {module: row for row, modules in enumerate(LAYERS) for module in modules}


def views_imported(source: str) -> list[str]:
    """The modules of views/ a file imports when it runs."""
    found = []
    for name in imported(source, PACKAGE)[0]:
        parts = name.split(".")
        if parts[:2] == ["adminsite", "views"] and len(parts) > 3:
            found.append(parts[2])
    return list(dict.fromkeys(found))


def reaching_up(module: str, source: str) -> list[str]:
    """The modules a file imports that sit in its own row or above it."""
    return [
        other
        for other in views_imported(source)
        if other in ROW and ROW[other] >= ROW[module]
    ]


MODULES = sorted(path for path in VIEWS.glob("*.py") if path.stem != "__init__")


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_every_module_has_a_row(path: Path) -> None:
    assert path.stem in ROW, (
        f"views/{path.name} has no row. Add it to LAYERS in "
        "tests/test_view_layers.py, above every module it imports, and to the "
        "map in AGENTS.md."
    )


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_a_module_imports_only_the_rows_below_it(path: Path) -> None:
    reached = reaching_up(path.stem, path.read_text(encoding="utf-8"))

    assert reached == [], (
        f"views/{path.name} imports {', '.join(reached)}, from its own row or "
        "one above it. Move what it needs down a row, or have ModelView hand "
        "it over."
    )


class TestTheCheckItself:
    def test_it_lets_a_module_import_a_row_below(self) -> None:
        source = "from adminsite.views._fields import ViewFields\n"

        assert reaching_up("_pages", source) == []

    def test_it_sees_a_module_reaching_up(self) -> None:
        source = "from adminsite.views._saver import Saver\n"

        assert reaching_up("_pages", source) == ["_saver"]

    def test_it_sees_a_module_beside_it(self) -> None:
        source = "from adminsite.views._audit import AuditRecorder\n"

        assert reaching_up("_reader", source) == ["_audit"]

    def test_it_sees_an_import_inside_a_function(self) -> None:
        source = "def build():\n    from adminsite.views.model_view import ModelView\n"

        assert reaching_up("_inline_views", source) == ["model_view"]

    def test_it_leaves_out_what_only_type_checkers_read(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from adminsite.views.model_view import ModelView\n"
        )

        assert reaching_up("_pages", source) == []
