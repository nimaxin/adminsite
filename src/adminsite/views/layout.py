"""How a view arranges its fields: in panels, fieldsets, rows and tabs."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, TypeAlias

from adminsite.columns import ColumnReference, is_column
from adminsite.exceptions import AdminSiteError

__all__ = [
    "FieldsetWidget",
    "LayoutEntry",
    "PanelWidget",
    "Placed",
    "RowWidget",
    "TabsWidget",
    "arrange",
    "read_layout",
]


@dataclass(frozen=True)
class PanelWidget:
    """Fields under a titled card, on the forms and the record page.

    ```python
    class ProductView(ModelView[Product]):
        form_layout = [
            PanelWidget("Product", [Product.name, Product.description]),
            PanelWidget("Price", [(Product.price, Product.cost)]),
        ]
    ```

    Args:
        title: The card's title. A panel holding one field leaves that
            field's label to it.
        children: The fields and widgets inside, in order: a tuple of them
            is a row and a list a column, as everywhere in a layout.
        description: A line under the title, saying what the fields are
            for.
        collapsible: Whether the panel can be folded away.
        collapsed: Whether it starts folded. A folded panel opens when it
            holds a mistake.
    """

    title: str
    children: Sequence["LayoutEntry"] = ()
    description: str = ""
    collapsible: bool = False
    collapsed: bool = False


@dataclass(frozen=True)
class FieldsetWidget:
    """Fields in a bordered group with a caption, inside a panel.

    Args:
        legend: The caption on the border.
        children: The fields and widgets inside, as a panel takes them.
    """

    legend: str
    children: Sequence["LayoutEntry"] = ()


@dataclass(frozen=True)
class RowWidget:
    """Fields side by side on a wide screen, one under the other on a phone.

    A tuple of fields is a row too: `(Product.price, Product.stock)`.

    Args:
        children: The fields and widgets side by side, in order.
    """

    children: Sequence["LayoutEntry"] = ()


@dataclass(frozen=True)
class TabsWidget:
    """Fields split into tabs, as (label, fields) pairs, one tab shown at a time.

    The form opens on the first tab holding a mistake, and marks each tab
    that holds one. The record page shows each tab as a panel of its own.

    Args:
        tabs: Each tab as a pair of its label and what it holds: a field, a
            widget, or a list of them.
    """

    tabs: Sequence[tuple[str, "LayoutEntry"]] = ()


# A field, by attribute or name, a widget, a tuple of entries for a row or a
# list of them for a column.
LayoutEntry: TypeAlias = (
    "ColumnReference | PanelWidget | FieldsetWidget | RowWidget | TabsWidget"
    " | tuple[LayoutEntry, ...] | list[LayoutEntry]"
)

Kind: TypeAlias = Literal["field", "panel", "fieldset", "row", "column", "tabs", "tab"]


@dataclass(frozen=True)
class Placed:
    """One part of a view's layout, its fields named by their paths.

    The pages draw these. A "group" holds the fields a layout left outside
    any panel, so each page can give them a card of their own.
    """

    kind: Kind | Literal["group"]
    path: str = ""
    title: str = ""
    description: str = ""
    collapsible: bool = False
    collapsed: bool = False
    children: tuple["Placed", ...] = ()
    # A panel's one field leaves its label to the panel's title.
    show_label: bool = True

    def paths(self) -> list[str]:
        """The paths of the fields inside, in the order they are drawn."""
        return list(self._paths())

    def _paths(self) -> Iterator[str]:
        if self.kind == "field":
            yield self.path
        for child in self.children:
            yield from child._paths()


def read_layout(
    entries: Sequence[Any],
    path_of: Callable[[Any, str], str],
    *,
    owner: str,
    where: str = "form_layout",
) -> tuple[Placed, ...]:
    """A layout as written, read into parts, each field turned into its path.

    `path_of` turns a field, by attribute or name, into its path, and says
    so when it cannot; `where` names the entry it was given, such as
    form_layout[1].children[0], for that message, and `owner` the view.
    """
    return tuple(
        _read(entry, path_of, owner, f"{where}[{index}]")
        for index, entry in enumerate(_listed(entries, owner, where))
    )


def _listed(entries: Any, owner: str, where: str) -> Sequence[Any]:
    """The entries of a list, refusing anything else where one belongs."""
    if isinstance(entries, list | tuple):
        return entries
    raise AdminSiteError(
        f"{owner}.{where} is {type(entries).__name__}; it takes a list of fields "
        "and widgets."
    )


def _read(
    entry: Any, path_of: Callable[[Any, str], str], owner: str, where: str
) -> Placed:
    if isinstance(entry, PanelWidget):
        return Placed(
            "panel",
            title=_text(entry.title, owner, f"{where}.title"),
            description=_text(entry.description, owner, f"{where}.description"),
            collapsible=entry.collapsible,
            collapsed=entry.collapsed,
            children=read_layout(
                entry.children, path_of, owner=owner, where=f"{where}.children"
            ),
        )
    if isinstance(entry, FieldsetWidget):
        return Placed(
            "fieldset",
            title=_text(entry.legend, owner, f"{where}.legend"),
            children=read_layout(
                entry.children, path_of, owner=owner, where=f"{where}.children"
            ),
        )
    if isinstance(entry, RowWidget):
        return Placed(
            "row",
            children=read_layout(
                entry.children, path_of, owner=owner, where=f"{where}.children"
            ),
        )
    if isinstance(entry, TabsWidget):
        tabs = []
        for index, pair in enumerate(_listed(entry.tabs, owner, f"{where}.tabs")):
            if not (isinstance(pair, tuple) and len(pair) == 2):
                raise AdminSiteError(
                    f"{owner}.{where}.tabs[{index}] is {pair!r}; each tab is a "
                    '(label, fields) pair, such as ("Price", [Product.price]).'
                )
            label, content = pair
            tabs.append(
                Placed(
                    "tab",
                    title=_text(label, owner, f"{where}.tabs[{index}]"),
                    children=(
                        _read(content, path_of, owner, f"{where}.tabs[{index}]"),
                    ),
                )
            )
        return Placed("tabs", children=tuple(tabs))
    # A string names a field, so it is read as one before a tuple or a list.
    if is_column(entry):
        return Placed("field", path=path_of(entry, where))
    if isinstance(entry, tuple):
        return Placed(
            "row", children=read_layout(entry, path_of, owner=owner, where=where)
        )
    if isinstance(entry, list):
        return Placed(
            "column", children=read_layout(entry, path_of, owner=owner, where=where)
        )
    raise AdminSiteError(
        f"{owner}.{where} is {type(entry).__name__}. A layout takes fields, "
        "PanelWidget, FieldsetWidget, RowWidget and TabsWidget, a tuple of them "
        "for a row and a list for a column."
    )


def _text(value: Any, owner: str, where: str) -> str:
    if not isinstance(value, str):
        raise AdminSiteError(
            f"{owner}.{where} is {type(value).__name__}; write it as text."
        )
    return value


def arrange(
    layout: Sequence[Placed], shown: Sequence[str], *, form: bool
) -> tuple[Placed, ...]:
    """The layout of one page: the fields it shows, where the view placed them.

    A field the page does not show is left out, and so is a part left with
    nothing in it. A field the layout leaves out comes last, in the order of
    `fields`, so none is ever lost. On a form, a panel holding one field
    hides that field's label, since its title already names it.

    Fields outside any panel or tabs are gathered into groups, one for each
    run of them, so a page can give each group a card.
    """
    visible = set(shown)
    placed = {path for part in layout for path in part.paths()}
    kept = [
        found for part in layout if (found := _keep(part, visible, form)) is not None
    ]
    kept.extend(Placed("field", path=path) for path in shown if path not in placed)
    cards: list[Placed] = []
    loose: list[Placed] = []
    for part in kept:
        if part.kind in ("panel", "tabs"):
            if loose:
                cards.append(Placed("group", children=tuple(loose)))
                loose = []
            cards.append(part)
        else:
            loose.append(part)
    if loose:
        cards.append(Placed("group", children=tuple(loose)))
    return tuple(cards)


def _keep(part: Placed, visible: set[str], form: bool) -> Placed | None:
    if part.kind == "field":
        return part if part.path in visible else None
    children = tuple(
        found
        for child in part.children
        if (found := _keep(child, visible, form)) is not None
    )
    if not children:
        return None
    if form and part.kind == "panel" and len(children) == 1:
        only = children[0]
        if only.kind == "field":
            children = (replace(only, show_label=False),)
    return replace(part, children=children)
