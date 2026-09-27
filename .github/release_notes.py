"""Write a release's notes from its section of CHANGELOG.md.

    python .github/release_notes.py 0.1.0a9 > notes.md

The changelog wraps its lines, and GitHub shows every line break in release
notes, so each paragraph and each point is joined back onto one line.
"""

import sys
from pathlib import Path

CHANGELOG = Path(__file__).parent.parent / "CHANGELOG.md"


def section(version: str) -> list[str]:
    """The lines under the version's heading, up to the next heading."""
    found: list[str] = []
    inside = False
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line == f"## {version}"
            continue
        if inside:
            found.append(line)
    return found


def unwrapped(lines: list[str]) -> list[str]:
    """Each paragraph and each point on one line."""
    joined: list[str] = []
    fenced = False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
            joined.append(line)
            continue
        starts_part = (
            fenced
            or not line.strip()
            or line.lstrip().startswith(("- ", "#"))
            or not joined
            or not joined[-1].strip()
            or joined[-1].startswith("```")
        )
        if starts_part:
            joined.append(line)
        else:
            joined[-1] = f"{joined[-1]} {line.strip()}"
    return joined


def main() -> int:
    """Print the notes for the version, or say why there are none."""
    version = sys.argv[1].removeprefix("v")
    lines = unwrapped(section(version))
    if not any(line.strip() for line in lines):
        message = f"CHANGELOG.md has no section for {version}. Add one and tag again."
        print(message, file=sys.stderr)
        return 1
    notes = ["```", f"pip install adminsite=={version}", "```", *lines]
    sys.stdout.write("\n".join(notes).strip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
