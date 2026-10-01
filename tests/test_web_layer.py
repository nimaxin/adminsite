"""The pages reach the database only through the view.

No module under http/ imports SQLAlchemy or adminsite's backend when it
runs. One may name the session's type under TYPE_CHECKING, for its
annotations, and nothing more from the backend. So every page reads through
the view, where the scope and the permissions apply, and sees adminsite's
own errors.
"""

import ast
from pathlib import Path

import pytest

HTTP = Path(__file__).parent.parent / "src" / "adminsite" / "http"
PACKAGE = "adminsite.http"
KEPT_OUT = ("sqlalchemy", "adminsite.backends")
# The one name from the backend a page may import, under TYPE_CHECKING only.
SESSION_TYPE = "adminsite.backends.sqlalchemy.session.SessionAdapter"


def is_type_checking(test: ast.expr) -> bool:
    """Whether an if tests TYPE_CHECKING, so its body never runs."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def absolute(node: ast.ImportFrom, package: str) -> str:
    """The module a from import names, with a relative one written out."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    # One dot is the package itself, and each further dot the one above.
    kept = parts[: len(parts) - node.level + 1]
    return ".".join([*kept, node.module] if node.module else kept)


def imported(source: str, package: str = PACKAGE) -> tuple[list[str], list[str]]:
    """The names a file imports when it runs, and those only type checkers read.

    A from import counts as the name it imports, written out in full, so
    `from adminsite import backends` reads as adminsite.backends.
    """
    run: list[str] = []
    typed: list[str] = []

    def visit(node: ast.AST, found: list[str]) -> None:
        if isinstance(node, ast.If) and is_type_checking(node.test):
            for checked in node.body:
                visit(checked, typed)
            for other in node.orelse:
                visit(other, found)
            return
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = absolute(node, package)
            found.extend(f"{module}.{alias.name}" for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child, found)

    visit(ast.parse(source), run)
    return run, typed


def imported_when_run(source: str, package: str = PACKAGE) -> list[str]:
    """The names a file imports when it runs, inside functions too."""
    return imported(source, package)[0]


def kept_out(module: str) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in KEPT_OUT)


def named_for_types_beyond_the_session(source: str) -> list[str]:
    """What a file imports from the backend under TYPE_CHECKING, but the session."""
    return [
        name for name in imported(source)[1] if kept_out(name) and name != SESSION_TYPE
    ]


PAGE_MODULES = sorted(HTTP.rglob("*.py"))


def page_module_name(path: Path) -> str:
    return path.relative_to(HTTP).as_posix()


@pytest.mark.parametrize("path", PAGE_MODULES, ids=page_module_name)
def test_a_page_module_never_imports_sqlalchemy(path: Path) -> None:
    reached = [
        module
        for module in imported_when_run(path.read_text(encoding="utf-8"))
        if kept_out(module)
    ]

    assert reached == [], (
        f"http/{page_module_name(path)} imports {', '.join(reached)}. Read "
        "through the view instead, and name the session's type under "
        "TYPE_CHECKING."
    )


@pytest.mark.parametrize("path", PAGE_MODULES, ids=page_module_name)
def test_a_page_module_names_only_the_session_type_for_types(path: Path) -> None:
    named = named_for_types_beyond_the_session(path.read_text(encoding="utf-8"))

    assert named == [], (
        f"http/{page_module_name(path)} names {', '.join(named)} under "
        "TYPE_CHECKING. Only SessionAdapter may be named there."
    )


class TestTheCheckItself:
    def test_it_sees_an_import_at_the_top(self) -> None:
        source = "from sqlalchemy.exc import IntegrityError\n"

        assert imported_when_run(source) == ["sqlalchemy.exc.IntegrityError"]

    def test_it_sees_an_import_inside_a_function(self) -> None:
        source = (
            "def read():\n"
            "    from adminsite.backends.sqlalchemy import SQLAlchemyRepository\n"
        )

        assert kept_out(imported_when_run(source)[0])

    def test_it_writes_out_a_relative_import(self) -> None:
        source = "from ..backends.sqlalchemy import session\n"

        assert imported_when_run(source) == ["adminsite.backends.sqlalchemy.session"]

    def test_it_sees_a_module_imported_from_its_package(self) -> None:
        written = imported_when_run("from adminsite import backends\n")
        relative = imported_when_run("from .. import backends\n")

        assert written == relative == ["adminsite.backends"]
        assert kept_out(written[0])

    def test_it_leaves_out_what_only_type_checkers_read(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from adminsite.backends.sqlalchemy.session import SessionAdapter\n"
        )

        assert imported_when_run(source) == ["typing.TYPE_CHECKING"]
        assert named_for_types_beyond_the_session(source) == []

    def test_only_the_session_type_is_named_for_types(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from sqlalchemy import Select\n"
            "    from adminsite.admin import Admin\n"
            "    from adminsite.backends.sqlalchemy.session import SessionAdapter\n"
        )

        assert named_for_types_beyond_the_session(source) == ["sqlalchemy.Select"]

    def test_it_sees_what_runs_when_type_checkers_do_not(self) -> None:
        source = (
            "import typing\n"
            "if typing.TYPE_CHECKING:\n"
            "    pass\n"
            "else:\n"
            "    import sqlalchemy\n"
        )

        assert imported_when_run(source) == ["typing", "sqlalchemy"]
