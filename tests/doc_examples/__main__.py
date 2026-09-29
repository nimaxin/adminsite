"""Type-check the Python examples in the docs, the README and the docstrings.

    uv run python -m tests.doc_examples

Each example becomes a module of its own, and mypy checks them all with the
project's settings. A name an example uses without defining comes from an
earlier example on the same page, or from context.py, which holds what the
examples take as given. An example that awaits outside a function is checked as
the body of one.
"""

import ast
import builtins
import importlib.util
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from mypy import api

from adminsite import ModelView

ROOT = Path(__file__).parents[2]
CONTEXT = Path(__file__).with_name("context.py")
CONTEXT_MODULE = "tests.doc_examples.context"
PACKAGE = "doc_blocks"
FENCE = re.compile(r"^(?P<indent>\s*)```(?P<language>\w*)\s*$")
REPORT = re.compile(r"^(?P<path>.*?\.py):(?P<line>\d+):(?P<rest>.*)$")
KNOWN = {*dir(builtins), "__file__", "__name__"}


@dataclass
class Example:
    """One python block, and where it is written."""

    source: Path
    line: int
    code: str
    module: str = ""
    header: int = 0

    @property
    def place(self) -> str:
        """The file and line the block's code starts on."""
        return self.at(1)

    def at(self, line: int) -> str:
        """The file and line of a line of the block, counted from 1."""
        return f"{self.source.relative_to(ROOT).as_posix()}:{self.line + line - 1}"


def blocks(text: str, first_line: int) -> Iterator[tuple[int, str]]:
    """Each python block in the text, with the line its code starts on."""
    opening: re.Match[str] | None = None
    start = 0
    body: list[str] = []
    for number, line in enumerate(text.splitlines(), start=first_line):
        fence = FENCE.match(line)
        if opening is None:
            if fence is not None:
                opening, start, body = fence, number + 1, []
        elif fence is not None and not fence["language"]:
            if opening["language"] == "python":
                yield start, "\n".join(body)
            opening = None
        else:
            body.append(line[len(opening["indent"]) :])


def examples() -> Iterator[Example]:
    """Every python block in the docs, the README and the package's docstrings."""
    for page in [*sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md"]:
        for line, code in blocks(page.read_text(encoding="utf-8"), 1):
            yield Example(page, line, code)
    for path in sorted((ROOT / "src" / "adminsite").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for line, code in blocks(node.value, node.lineno):
                    yield Example(path, line, code)


def bound(tree: ast.AST) -> set[str]:
    """Every name the code binds, at any depth."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update(imported(node))
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def exported(tree: ast.Module) -> set[str]:
    """The names the code binds at the top of its module."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update(imported(node))
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(
                name.id
                for target in targets
                for name in ast.walk(target)
                if isinstance(name, ast.Name)
            )
    return names


def imported(node: ast.Import | ast.ImportFrom) -> set[str]:
    """The names an import binds."""
    return {(alias.asname or alias.name).split(".")[0] for alias in node.names}


def used(tree: ast.AST) -> set[str]:
    """Every name the code reads."""
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def awaits_outside_a_function(code: str) -> bool:
    """Whether the code awaits at the top, as the body of a hook does."""
    try:
        compile(code, "<example>", "exec")
    except SyntaxError as error:
        message = str(error.msg)
        return "outside" in message and ("await" in message or "async" in message)
    return False


def import_lines(tree: ast.Module) -> dict[str, str]:
    """Each name the code imports at its top, with an import of that name alone."""
    lines: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                alone: ast.stmt = ast.Import(names=[alias])
                if isinstance(node, ast.ImportFrom):
                    alone = ast.ImportFrom(node.module, [alias], node.level)
                name = (alias.asname or alias.name).split(".")[0]
                lines[name] = ast.unparse(alone)
    return lines


def missing_packages(tree: ast.Module) -> list[str]:
    """The packages the code imports that are not installed here."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module)
    packages = {module.split(".")[0] for module in modules}
    return sorted(name for name in packages if importlib.util.find_spec(name) is None)


def unknown_settings(example: Example, tree: ast.Module) -> Iterator[str]:
    """Each setting a view in the code sets that ModelView does not have.

    mypy takes any class attribute, so Django's list_display would pass it,
    though the admin refuses it when it starts.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            ast.unparse(base).startswith("ModelView") for base in node.bases
        ):
            for statement in node.body:
                if isinstance(statement, ast.Assign | ast.AnnAssign):
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name) and not hasattr(
                            ModelView, target.id
                        ):
                            yield (
                                f"{example.at(statement.lineno)}: ModelView has "
                                f"no setting {target.id}"
                            )


def written(example: Example, header: list[str]) -> str:
    """The module mypy reads for the example."""
    code = example.code
    if awaits_outside_a_function(code):
        header = [*header, "async def example() -> None:"]
        code = "\n".join(f"    {line}" for line in code.splitlines())
    example.header = len(header)
    return "\n".join([*header, code, ""])


def placed(report: str, by_module: dict[str, Example]) -> str:
    """The mypy line, pointing at the page or docstring the example is in."""
    match = REPORT.match(report)
    example = by_module.get(Path(match["path"]).stem) if match else None
    if match is None or example is None:
        return report
    line = max(int(match["line"]) - example.header, 1)
    return f"{example.at(line)}:{match['rest']}"


def main() -> int:
    """Check every example, and print each problem where it is written."""
    given = set(ast.literal_eval(_all_of(ast.parse(CONTEXT.read_text("utf-8")))))
    problems: list[str] = []
    skipped: list[str] = []
    by_module: dict[str, Example] = {}
    # For each page, the import that brings each name its examples defined.
    earlier: dict[Path, dict[str, str]] = {}

    with tempfile.TemporaryDirectory() as folder:
        package = Path(folder) / PACKAGE
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")

        for index, example in enumerate(examples()):
            try:
                tree = ast.parse(example.code)
            except SyntaxError as error:
                problems.append(f"{example.place}: {error.msg}, line {error.lineno}")
                continue
            if missing := missing_packages(tree):
                skipped.append(f"{example.place}: {', '.join(missing)} not installed")
                continue
            problems.extend(unknown_settings(example, tree))
            on_this_page = earlier.setdefault(example.source, {})
            header: list[str] = []
            for name in sorted(used(tree) - bound(tree) - KNOWN):
                if name in on_this_page:
                    header.append(on_this_page[name])
                elif name in given:
                    header.append(f"from {CONTEXT_MODULE} import {name}")
                else:
                    problems.append(
                        f"{example.place}: {name} is not defined there, in an "
                        "earlier example on the page, or in "
                        "tests/doc_examples/context.py"
                    )

            example.module = f"example_{index}"
            by_module[example.module] = example
            (package / f"{example.module}.py").write_text(
                written(example, header), encoding="utf-8"
            )
            if not awaits_outside_a_function(example.code):
                module = f"{PACKAGE}.{example.module}"
                for name in exported(tree):
                    on_this_page[name] = f"from {module} import {name}"
            on_this_page.update(import_lines(tree))

        os.environ["MYPYPATH"] = str(ROOT)
        output, errors, status = api.run(
            [
                "--config-file",
                str(ROOT / "pyproject.toml"),
                # An example reads request.state.user, which Starlette leaves
                # untyped, and returns what it says without a cast.
                "--disable-error-code",
                "no-any-return",
                "--hide-error-context",
                "--no-error-summary",
                str(package),
            ]
        )
        problems.extend(placed(report, by_module) for report in output.splitlines())
        if errors.strip():
            problems.append(errors.strip())

    for problem in problems:
        print(problem)
    for note in skipped:
        print(f"{note}, so not checked")
    if problems or status != 0:
        return 1
    print(f"{len(by_module)} examples type-check.")
    return 0


def _all_of(tree: ast.Module) -> ast.expr:
    """The value the module gives __all__."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            return node.value
    raise LookupError("context.py has no __all__")


if __name__ == "__main__":
    sys.exit(main())
