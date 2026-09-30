"""The pages reach the database only through the view.

No module under http/ imports SQLAlchemy or adminsite's backend when it
runs. One may name the session's type under TYPE_CHECKING, for its
annotations, and nothing more. So every page reads through the view, where
the scope and the permissions apply, and sees adminsite's own errors.
"""

import ast
from pathlib import Path

import pytest

HTTP = Path(__file__).parent.parent / "src" / "adminsite" / "http"
PACKAGE = "adminsite.http"
KEPT_OUT = ("sqlalchemy", "adminsite.backends")


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


def imported_when_run(source: str, package: str = PACKAGE) -> list[str]:
    """The modules a file imports when it runs, inside functions too."""
    found: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and is_type_checking(node.test):
            for other in node.orelse:
                visit(other)
            return
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(absolute(node, package))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(source))
    return found


def kept_out(module: str) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in KEPT_OUT)


@pytest.mark.parametrize("path", sorted(HTTP.glob("*.py")), ids=lambda path: path.name)
def test_a_page_module_never_imports_sqlalchemy(path: Path) -> None:
    reached = [
        module
        for module in imported_when_run(path.read_text(encoding="utf-8"))
        if kept_out(module)
    ]

    assert reached == [], (
        f"http/{path.name} imports {', '.join(reached)}. Read through the view "
        "instead, and name the session's type under TYPE_CHECKING."
    )


class TestTheCheckItself:
    def test_it_sees_an_import_at_the_top(self) -> None:
        source = "from sqlalchemy.exc import IntegrityError\n"

        assert imported_when_run(source) == ["sqlalchemy.exc"]

    def test_it_sees_an_import_inside_a_function(self) -> None:
        source = (
            "def read():\n"
            "    from adminsite.backends.sqlalchemy import SQLAlchemyRepository\n"
        )

        assert kept_out(imported_when_run(source)[0])

    def test_it_writes_out_a_relative_import(self) -> None:
        source = "from ..backends.sqlalchemy import session\n"

        assert imported_when_run(source) == ["adminsite.backends.sqlalchemy"]

    def test_it_leaves_out_what_only_type_checkers_read(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from adminsite.backends.sqlalchemy.session import SessionAdapter\n"
        )

        assert imported_when_run(source) == ["typing"]

    def test_it_sees_what_runs_when_type_checkers_do_not(self) -> None:
        source = (
            "import typing\n"
            "if typing.TYPE_CHECKING:\n"
            "    pass\n"
            "else:\n"
            "    import sqlalchemy\n"
        )

        assert imported_when_run(source) == ["typing", "sqlalchemy"]
