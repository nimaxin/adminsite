"""Every module says what it offers in __all__, and offers nothing it imports.

A package hands on what it imports; any other module offers only what it
defines, so an editor completing adminsite.files never offers Path or re.
"""

import ast
import importlib
import inspect
import pkgutil
from types import FunctionType, ModuleType

import pytest

import adminsite


def modules() -> list[ModuleType]:
    names = [adminsite.__name__] + [
        found.name for found in pkgutil.walk_packages(adminsite.__path__, "adminsite.")
    ]
    return [importlib.import_module(name) for name in names]


def imported_names(module: ModuleType) -> set[str]:
    """The names a module's own import statements bind."""
    tree = ast.parse(inspect.getsource(module))
    return {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }


@pytest.mark.parametrize("module", modules(), ids=lambda module: module.__name__)
def test_a_module_lists_what_it_offers(module: ModuleType) -> None:
    offered = vars(module).get("__all__")

    assert offered is not None, f"{module.__name__} has no __all__"
    imported = set() if hasattr(module, "__path__") else imported_names(module)
    for name in offered:
        assert hasattr(module, name), f"{module.__name__}.{name} does not exist"
        assert name not in imported, (
            f"{module.__name__} offers {name}, which it only imports"
        )


@pytest.mark.parametrize("module", modules(), ids=lambda module: module.__name__)
def test_each_public_class_and_function_is_offered(module: ModuleType) -> None:
    offered = set(vars(module).get("__all__", ()))
    missing = [
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and isinstance(value, type | FunctionType)
        and value.__module__ == module.__name__
        and name not in offered
    ]

    assert missing == [], f"{module.__name__} leaves out {missing}"
