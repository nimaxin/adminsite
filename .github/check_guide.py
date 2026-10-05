"""Check that each built wheel carries the docs' whole guide for coding agents.

    uv run --group docs mkdocs build --strict
    cp site/llms-full.txt src/adminsite/llms-full.txt
    uv build
    python .github/check_guide.py

The release copies the guide into the package before it builds, so the wheel
holds the guide for its own version; CI does the same on every pull request.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
BUILT = ROOT / "site" / "llms-full.txt"
PACKED = "adminsite/llms-full.txt"


def problem_with(wheel: Path, guide: bytes) -> str:
    """What is wrong with the guide in this wheel, or nothing."""
    with zipfile.ZipFile(wheel) as archive:
        if PACKED not in archive.namelist():
            return (
                f"{wheel.name} has no {PACKED}. Copy site/llms-full.txt into "
                "src/adminsite/ before uv build."
            )
        if archive.read(PACKED) != guide:
            return f"{wheel.name} holds a {PACKED} unlike site/llms-full.txt."
    return ""


def main() -> int:
    """Check each wheel in dist/, saying what is wrong with the first that fails."""
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    if not wheels:
        print("dist/ holds no wheel. Run uv build first.")
        return 1
    guide = BUILT.read_bytes()
    for wheel in wheels:
        problem = problem_with(wheel, guide)
        if problem:
            print(problem)
            return 1
        print(f"{wheel.name} carries the guide, {len(guide):,} bytes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
