"""The type mistakes in tests/reference are refused for the reason each gives.

An ignore with an error code proves only that mypy reports some error of that
code on the line. This test reads each file again without its ignores and
checks what mypy says there, so a line refused for another reason fails.
"""

import re
from pathlib import Path

import pytest
from mypy import api

ROOT = Path(__file__).parent.parent
REFERENCE = ROOT / "tests" / "reference"
FILES = ("field_mistakes.py", "mistakes.py")
IGNORE = re.compile(r"\s*# type: ignore\[(?P<codes>[^\]]+)\].*$")
REPORT = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): error: (?P<message>.+)  \[(?P<code>[a-z-]+)\]$"
)

# What mypy has to say about each mistake, found by a piece of its line. A
# line holding two pieces has to be refused twice, once for each.
EXPECTED = {
    # field_mistakes.py
    "TextAreaField(Order.total)": (
        'Argument 1 to "TextAreaField" has incompatible type '
        '"InstrumentedAttribute[Decimal]"'
    ),
    'lable="Note"': 'Unexpected keyword argument "lable" for "Field"',
    'Field(Order.note, "Note")': 'Too many positional arguments for "Field"',
    'read_only="yes"': 'Argument "read_only" to "Field" has incompatible type "str"',
    '"pink"': 'Dict entry 0 has incompatible type "OrderStatus": "Literal[\'pink\']"',
    "Order.totl": '"type[Order]" has no attribute "totl"',
    'ComputedField("name", customer_name)': (
        'Argument 2 to "ComputedField" has incompatible type '
        '"Callable[[Customer], str]"'
    ),
    "Order.created_at.desc()": 'List item 0 has incompatible type "UnaryExpression',
    "sortable_fields = [Descending(Order.total)]": (
        'List item 0 has incompatible type "Descending"'
    ),
    "fields = Order.id": (
        'Incompatible types in assignment (expression has type "int"'
    ),
    # A SQLModel attribute is typed as the value it holds; col() names the column.
    "[Book.pages]": 'List item 0 has incompatible type "int | None"',
    "col(Book.pages).desc()": 'List item 0 has incompatible type "UnaryExpression',
    # mypy reads col() by what TextAreaField wants, so it refuses col's argument.
    "TextAreaField(col(Book.price))": (
        'Argument 1 to "col" has incompatible type "Decimal"; expected "str | None"'
    ),
    # mistakes.py
    "record: Customer": (
        'Argument 1 of "get_record_title" is incompatible with supertype'
    ),
    # The query's own type differs between SQLAlchemy 2.0 and 2.1.
    "return select(Order)": 'expected "Statement"',
    'set("a lot")': (
        'Argument 1 to "set" of "SaveValue" has incompatible type "str"; '
        'expected "Decimal"'
    ),
    'on="records"': 'Argument "on" to "action" has incompatible type',
}

Reported = dict[str, dict[int, list[tuple[str, str]]]]


def without_ignores(source: str) -> str:
    """The source with every type ignore taken off, each line where it was."""
    return "\n".join(IGNORE.sub("", line) for line in source.splitlines()) + "\n"


@pytest.fixture(scope="module")
def reported(tmp_path_factory: pytest.TempPathFactory) -> Reported:
    """mypy's errors in each file without its ignores: code and message by line."""
    folder = tmp_path_factory.mktemp("mistakes")
    for name in FILES:
        source = (REFERENCE / name).read_text(encoding="utf-8")
        (folder / name).write_text(without_ignores(source), encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        # From the root, the files import tests.reference.models, and mypy
        # reuses what `mypy src tests` left in its cache.
        patch.chdir(ROOT)
        output, errors, _ = api.run(
            [
                "--config-file",
                str(ROOT / "pyproject.toml"),
                "--hide-error-context",
                "--no-error-summary",
                *(str(folder / name) for name in FILES),
            ]
        )
    assert not errors.strip(), errors
    found: Reported = {name: {} for name in FILES}
    for report in output.splitlines():
        match = REPORT.match(report)
        if match is not None:
            on_line = found[Path(match["path"]).name].setdefault(int(match["line"]), [])
            on_line.append((match["code"], match["message"]))
    return found


@pytest.mark.parametrize("name", FILES)
def test_each_mistake_is_refused_for_its_reason(name: str, reported: Reported) -> None:
    lines = (REFERENCE / name).read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        ignored = IGNORE.search(line)
        errors = reported[name].get(number, [])
        if ignored is None:
            assert errors == [], f"{name}:{number} has errors it does not mark"
            continue
        written = IGNORE.sub("", line)
        pieces = [piece for piece in EXPECTED if piece in written]
        assert pieces, f"{name}:{number} needs what mypy says there in EXPECTED."
        codes = {item.strip() for item in ignored["codes"].split(",")}
        unmatched = list(errors)
        for piece in pieces:
            matching = [
                error
                for error in unmatched
                if error[0] in codes and EXPECTED[piece] in error[1]
            ]
            assert matching, f"{name}:{number} should say {EXPECTED[piece]!r}: {errors}"
            unmatched.remove(matching[0])
        assert unmatched == [], f"{name}:{number} also says {unmatched}"


def test_every_expected_message_belongs_to_a_mistake() -> None:
    marked = [
        IGNORE.sub("", line)
        for name in FILES
        for line in (REFERENCE / name).read_text(encoding="utf-8").splitlines()
        if IGNORE.search(line)
    ]

    assert [
        piece for piece in EXPECTED if not any(piece in line for line in marked)
    ] == []
