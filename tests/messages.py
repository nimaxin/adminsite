"""Find every piece of text adminsite translates.

    uv run python -m tests.messages fa

prints the texts the Persian catalog is missing, as JSON ready to fill in. A
text with a count comes with a place for each plural form the language uses.
"""

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

from adminsite.i18n import plural_categories

PACKAGE = Path(__file__).parent.parent / "src" / "adminsite"

# A text in a template, in single or double quotes.
QUOTED = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
TEMPLATE_CALL = re.compile(r"\b_\(\s*" + QUOTED)
# A text with a count: ngettext(singular, plural, count), or plural_forms for
# a count the page changes.
TEMPLATE_PLURAL = re.compile(
    r"\b(?:ngettext|plural_forms)\(\s*" + QUOTED + r"\s*,\s*" + QUOTED
)

# Text passed to _() through a variable, so no search can see it.
INDIRECT = [
    "Enter a valid value.",
    "Enter some text.",
    "Enter a whole number.",
    "Enter a number.",
    "Enter an amount, for example 12.50.",
    "Choose yes or no.",
    "Enter a valid UUID.",
    "Choose one of the listed options.",
    "Choose a record.",
    "Enter a date, for example 2026-09-18.",
    "Enter a date and time, for example 2026-09-18 14:30.",
    "Enter a time, for example 14:30.",
    "Yes",
    "No",
    "Today",
    "Last 7 days",
    "Last 30 days",
    "Last 90 days",
    "Not allowed",
    "Not found",
    "Something went wrong",
    "view",
    "create",
    "edit",
    "delete",
    "export",
    "import",
    "history",
    "open",
    # The months, as a date is written: Sep 8, 2026.
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _quoted(match: re.Match[str], first: int) -> str:
    """The text a match found in whichever of its two kinds of quotes."""
    found = match.group(first)
    return found if found is not None else match.group(first + 1)


def from_templates() -> dict[str, str | None]:
    """Every text the templates translate, with its English plural if it has one."""
    found: dict[str, str | None] = {}
    for path in (PACKAGE / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in TEMPLATE_CALL.finditer(text):
            found.setdefault(_quoted(match, 1), None)
        for match in TEMPLATE_PLURAL.finditer(text):
            found[_quoted(match, 1)] = _quoted(match, 3)
    return found


def _text(node: ast.expr) -> str | None:
    """The text an argument holds, if it is written out."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def from_code() -> dict[str, str | None]:
    """Every text the Python code translates, joined as Python joins them."""
    found: dict[str, str | None] = {}
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "_" and node.args:
                text = _text(node.args[0])
                if text:
                    found.setdefault(text, None)
            if node.func.id == "ngettext" and len(node.args) >= 2:
                singular, plural = _text(node.args[0]), _text(node.args[1])
                if singular and plural:
                    found[singular] = plural
    return found


def every_message() -> dict[str, str | None]:
    """All the English text a catalog should cover, each with its plural if any."""
    return {**dict.fromkeys(INDIRECT), **from_code(), **from_templates()}


def plural_texts() -> dict[str, str]:
    """The texts with a count, each with its English plural."""
    return {
        text: plural for text, plural in every_message().items() if plural is not None
    }


def catalog(language: str) -> dict[str, Any]:
    """A shipped catalog, as its JSON file holds it."""
    found: dict[str, Any] = json.loads(
        (PACKAGE / "locales" / f"{language}.json").read_text(encoding="utf-8")
    )
    return found


def complete(language: str, text: str, translation: Any, plural: bool) -> bool:
    """Whether a translation covers a text: one with a count, in every form."""
    if not translation:
        return False
    categories = plural_categories(language)
    if isinstance(translation, str):
        return not plural or categories == ["other"]
    return (
        plural
        and isinstance(translation, dict)
        and all(translation.get(category) for category in categories)
    )


def missing(language: str) -> list[str]:
    """The texts a shipped catalog has no translation for, or not in every form."""
    found = catalog(language)
    plural = plural_texts()
    return sorted(
        text
        for text in every_message()
        if not complete(language, text, found.get(text), text in plural)
    )


def to_fill(language: str) -> dict[str, Any]:
    """The texts a catalog is missing, with an empty place for each translation."""
    plural = plural_texts()
    return {
        text: dict.fromkeys(plural_categories(language), "") if text in plural else ""
        for text in missing(language)
    }


if __name__ == "__main__":
    wanted = to_fill(sys.argv[1] if len(sys.argv) > 1 else "fa")
    print(json.dumps(wanted, ensure_ascii=False, indent=2))
