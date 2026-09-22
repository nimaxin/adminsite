"""Find every piece of text adminsite translates.

    uv run python -m tests.messages fa

prints the texts the Persian catalog is missing, as JSON ready to fill in.
"""

import ast
import json
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "src" / "adminsite"

TEMPLATE_CALL = re.compile(r"""_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")

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
]


def from_templates() -> set[str]:
    """Every _('...') in the templates."""
    found = set()
    for path in (PACKAGE / "templates").rglob("*.html"):
        for match in TEMPLATE_CALL.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1) if match.group(1) is not None else match.group(2))
    return found


def from_code() -> set[str]:
    """Every _("...") in the Python code, joined as Python joins them."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    return found


def every_message() -> set[str]:
    """All the English text a catalog should cover."""
    return from_templates() | from_code() | set(INDIRECT)


def missing(language: str) -> list[str]:
    """The texts a shipped catalog has no translation for."""
    catalog = json.loads(
        (PACKAGE / "locales" / f"{language}.json").read_text(encoding="utf-8")
    )
    return sorted(text for text in every_message() if not catalog.get(text))


if __name__ == "__main__":
    wanted = missing(sys.argv[1] if len(sys.argv) > 1 else "fa")
    print(json.dumps(dict.fromkeys(wanted, ""), ensure_ascii=False, indent=2))
