"""Each box in the templates that scrolls keeps to itself.

It holds what is placed inside it, and scrolls only the ways it is meant to.
Text kept for screen readers, such as "Not set" in an empty cell, is placed
against its nearest positioned ancestor, and only the boxes between the two
cut it off. In a box that scrolls but is not positioned itself, it lands
outside the box and stretches whatever scrolls around it: the page grew
scrollbars of its own around a table that already had them.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "src" / "adminsite" / "templates"
SCROLLING = {
    "overflow-auto",
    "overflow-scroll",
    "overflow-x-auto",
    "overflow-x-scroll",
    "overflow-y-auto",
    "overflow-y-scroll",
}
POSITIONED = {"relative", "absolute", "fixed", "sticky"}
# A box told to scroll one way scrolls the other way too, unless told not to.
OTHER_WAY = {
    "overflow-x-auto": "overflow-y-hidden",
    "overflow-x-scroll": "overflow-y-hidden",
    "overflow-y-auto": "overflow-x-hidden",
    "overflow-y-scroll": "overflow-x-hidden",
}


def scrolling_boxes() -> list[tuple[str, set[str]]]:
    """The classes of each box in the templates that scrolls, by template."""
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        for classes in re.findall(r'\bclass="([^"]*)"', source):
            names = set(classes.split())
            if {name.rsplit(":", 1)[-1] for name in names} & SCROLLING:
                found.append((path.relative_to(TEMPLATES).as_posix(), names))
    return found


def described(template: str, names: set[str]) -> str:
    return f"{template}: {' '.join(sorted(names))}"


def test_a_box_that_scrolls_is_positioned() -> None:
    boxes = scrolling_boxes()
    loose = [described(*box) for box in boxes if not box[1] & POSITIONED]

    assert len(boxes) > 10
    assert loose == [], "Make each of these `relative`."


def test_a_box_that_scrolls_one_way_never_scrolls_the_other() -> None:
    # Tabs that stood a pixel below their bar, which scrolls sideways, made it
    # scroll up and down by that pixel, with a scrollbar beside them.
    loose = [
        described(template, names)
        for template, names in scrolling_boxes()
        if any(way in names and other not in names for way, other in OTHER_WAY.items())
    ]

    assert loose == [], "Stop each of these scrolling the other way."
