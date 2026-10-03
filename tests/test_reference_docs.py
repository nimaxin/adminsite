"""Everything the Reference page shows says what it takes and what it holds.

The page is built from docstrings, and an editor shows the same docstrings
on hover. A parameter is listed under `Args:`, the constructor's in the
class docstring. A value someone sets or reads on a class, such as a
setting of `ModelView`, has a docstring written under it, or a line under
`Attributes:`. A function that answers something says what under `Returns:`.
A parameter a class inherits may be listed on the class it comes from.
"""

import ast
import enum
import importlib
import inspect
import re
import textwrap
from collections.abc import Callable, Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

REFERENCE = Path(__file__).parent.parent / "docs" / "reference.md"
# Written up one step at a time; each step takes its objects off this list.
NOT_YET: set[str] = {
    "adminsite.RequestAction",
    "adminsite.Link",
    "adminsite.Descending",
    "adminsite.Inline",
    "adminsite.PanelWidget",
    "adminsite.FieldsetWidget",
    "adminsite.RowWidget",
    "adminsite.TabsWidget",
    "adminsite.CountMode",
    "adminsite.Pagination",
    "adminsite.SaveContext",
    "adminsite.views.SaveValues",
    "adminsite.views.SaveValue",
    "adminsite.DeleteContext",
    "adminsite.actions.action.action",
    "adminsite.actions.Selection",
    "adminsite.actions.Input",
    "adminsite.Message",
    "adminsite.backends.sqlalchemy.SQLFilter",
    "adminsite.filters.FilterOption",
    "adminsite.filters.FilterValue",
    "adminsite.files.FileStorage",
    "adminsite.files.LocalStorage",
    "adminsite.AdminPage",
    "adminsite.Plugin",
    "adminsite.Widget",
    "adminsite.Stat",
    "adminsite.Chart",
    "adminsite.RecentRecords",
    "adminsite.ModelCounts",
    "adminsite.SavedViews",
    "adminsite.i18n.gettext",
    "adminsite.auth.AuthProvider",
    "adminsite.auth.PasswordAuth",
    "adminsite.auth.hash_password",
    "adminsite.audit.AuditLog",
    "adminsite.audit.AuditStore",
    "adminsite.audit.AuditQuery",
    "adminsite.audit.AuditEntry",
    "adminsite.RefusedError",
    "adminsite.PermissionDeniedError",
    "adminsite.RecordNotFoundError",
}


def resolve(target: str) -> Any:
    """The object a `:::` line names, such as adminsite.actions.action.action."""
    parts = target.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            found: Any = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for name in parts[cut:]:
            found = getattr(found, name)
        return found
    raise LookupError(target)


def shown() -> Iterator[tuple[str, list[str] | None]]:
    """Each object the page shows, with the members it names, if it names any.

    `members: false` shows none, and no `members` at all shows every one.
    """
    text = REFERENCE.read_text(encoding="utf-8")
    for block in re.split(r"^::: ", text, flags=re.MULTILINE)[1:]:
        target, *rest = block.split("\n")
        options = "\n".join(line for line in rest[: _indented(rest)])
        listed: list[str] | None = re.findall(
            r"^ +- (\w+)$", options, flags=re.MULTILINE
        )
        inline = re.search(r"members: \[(.*)\]", options)
        if inline:
            listed = [name.strip() for name in inline.group(1).split(",")]
        elif not listed and not re.search(r"members: false", options):
            listed = None
        yield target.strip(), listed


def _indented(lines: list[str]) -> int:
    """How many of the lines belong to the `:::` line above them."""
    for count, line in enumerate(lines):
        if line and not line.startswith(" "):
            return count
    return len(lines)


def listed_under(doc: str, heading: str) -> set[str]:
    """The names listed under one heading of a Google style docstring."""
    names: set[str] = set()
    inside = False
    for line in doc.splitlines():
        if not line.strip():
            continue
        if not line.startswith(" "):
            inside = line.strip() == f"{heading}:"
        elif inside and (found := re.match(r"    (\*{0,2}\w+)( \(.*\))?:", line)):
            names.add(found.group(1).lstrip("*"))
    return names


def documented_attributes(owner: type) -> set[str]:
    """The class attributes with a docstring written under them."""
    try:
        source = textwrap.dedent(inspect.getsource(owner))
    except (OSError, TypeError):
        return set()
    body = next(
        node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
    ).body
    found: set[str] = set()
    for node, after in pairwise(body):
        if not (
            isinstance(after, ast.Expr)
            and isinstance(after.value, ast.Constant)
            and isinstance(after.value.value, str)
        ):
            continue
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
        elif isinstance(node, ast.Assign):
            found.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return found


def ours(owner: type) -> list[type]:
    """The class and the classes of adminsite it is built on."""
    return [base for base in owner.__mro__ if base.__module__.startswith("adminsite")]


def described(owner: type) -> set[str]:
    """What a class and its own bases describe, by name."""
    names: set[str] = set()
    for base in ours(owner):
        doc = inspect.cleandoc(base.__doc__ or "")
        names |= listed_under(doc, "Args") | listed_under(doc, "Attributes")
        names |= documented_attributes(base)
    return names


def constructor_parameters(owner: type) -> list[str]:
    """The parameters people build the class with, if adminsite wrote them."""
    maker = next(
        (
            base.__dict__[name]
            for base in owner.__mro__
            for name in ("__init__", "__new__")
            if name in base.__dict__
        ),
        None,
    )
    if maker is None or not getattr(maker, "__module__", "").startswith("adminsite"):
        return []
    return list(inspect.signature(owner).parameters)


def class_gaps(owner: type) -> list[str]:
    """What a class leaves undescribed: parameters, settings, members of an Enum."""
    known = described(owner)
    if issubclass(owner, enum.Enum):
        wanted = list(owner.__members__)
    else:
        parameters = constructor_parameters(owner)
        settings = [
            name
            for name in inspect.get_annotations(owner)
            if not name.startswith("_") and name not in parameters
        ]
        wanted = parameters + settings
    return [f"{owner.__name__}: {name}" for name in wanted if name not in known]


def function_gaps(label: str, function: Callable[..., Any]) -> list[str]:
    """The parameters a function leaves out of `Args:`, and a missing `Returns:`."""
    doc = inspect.getdoc(function) or ""
    signature = inspect.signature(function)
    parameters = [name for name in signature.parameters if name not in ("self", "cls")]
    listed = listed_under(doc, "Args")
    problems = [f"{label}: {name}" for name in parameters if name not in listed]
    problems += [
        f"{label}: Args names {name}, which it does not take"
        for name in sorted(listed - set(parameters))
    ]
    answers = signature.return_annotation not in (None, "None", signature.empty)
    if answers and not re.search(r"^(Returns|Yields):$", doc, flags=re.MULTILINE):
        problems.append(f"{label}: Returns")
    return problems


def gaps(target: str, members: list[str] | None) -> list[str]:
    """Everything one object on the page leaves undescribed."""
    found = resolve(target)
    if inspect.isfunction(found):
        return function_gaps(found.__name__, found)
    if not inspect.isclass(found):
        # A type alias or a type variable, described where it is written.
        return []
    problems = class_gaps(found)
    if members is None:
        members = [name for name in vars(found) if not name.startswith("_")]
    for name in members:
        member = inspect.getattr_static(found, name, None)
        if isinstance(member, staticmethod | classmethod):
            member = member.__func__
        if inspect.isfunction(member):
            problems += function_gaps(f"{found.__name__}.{name}", member)
    return problems


SHOWN = list(shown())


@pytest.mark.parametrize(
    ("target", "members"), SHOWN, ids=[target for target, _ in SHOWN]
)
def test_describes_what_it_takes_and_holds(
    target: str, members: list[str] | None
) -> None:
    if target in NOT_YET:
        pytest.skip("not written up yet")
    assert gaps(target, members) == []


def test_not_yet_lists_only_what_the_page_shows() -> None:
    assert NOT_YET.issubset(target for target, _ in SHOWN)
