"""The fields of the Users and Roles pages, the permission grid among them."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields import (
    BooleanField,
    DateTimeField,
    JSONField,
    PasswordField,
    RelationField,
    StringField,
    TextAreaField,
)
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.views import ModelView

__all__ = [
    "OWN_VIEWS",
    "Grid",
    "GridBox",
    "GridGroup",
    "GridLine",
    "OwnLinks",
    "OwnLongText",
    "OwnPassword",
    "OwnSwitch",
    "OwnText",
    "OwnTime",
    "OwnWords",
    "PermissionsField",
]

# The names of the Users and Roles views, which only superusers open, so no
# role can grant them.
OWN_VIEWS = frozenset({"admin_users", "admin_roles"})

# The permissions every view has a column for, as `Permission` names them.
STANDARD = frozenset(permission.value for permission in Permission)


class OwnWords:
    """A label in adminsite's own words, read in the language of each request.

    The field keeps the English, and every read translates it, so one view
    serves people reading in different languages at once.
    """

    _english: str

    @property
    def label(self) -> str:
        """The label, in the language of the request being answered."""
        return _(self._english)

    @label.setter
    def label(self, value: str) -> None:
        self._english = value


class OwnText(OwnWords, StringField):
    """A line of text, labelled in adminsite's own words."""


class OwnLongText(OwnWords, TextAreaField):
    """Text over several lines, labelled in adminsite's own words."""


class OwnPassword(OwnWords, PasswordField):
    """A password to set, labelled in adminsite's own words."""


class OwnSwitch(OwnWords, BooleanField):
    """A yes or no, labelled in adminsite's own words."""


class OwnTime(OwnWords, DateTimeField):
    """A date and time, labelled in adminsite's own words."""


class OwnLinks(OwnWords, RelationField):
    """Links to other records, labelled in adminsite's own words."""


@dataclass(frozen=True)
class GridBox:
    """One box of the grid: a permission on one view or page."""

    permission: str
    label: str
    checked: bool


@dataclass(frozen=True)
class GridLine:
    """One view or page, with a box under each column it offers."""

    name: str
    label: str
    # One for each column of the grid, None where the view offers none.
    boxes: list[GridBox | None]
    # The permissions its actions ask for, beyond the columns.
    actions: list[GridBox]


@dataclass(frozen=True)
class GridGroup:
    """The views of one sidebar group, or the pages of your own."""

    label: str
    lines: list[GridLine]


@dataclass(frozen=True)
class Grid:
    """What a role grants, laid out as the form draws it."""

    columns: list[tuple[str, str]]
    groups: list[GridGroup]
    granted: dict[str, list[str]]

    @property
    def has_actions(self) -> bool:
        """Whether any line has a permission an action asks for."""
        return any(line.actions for group in self.groups for line in group.lines)

    @property
    def json(self) -> str:
        """What is granted, as the form sends it."""
        return json.dumps(self.granted, ensure_ascii=False)


def _read_grants(value: Any) -> dict[str, set[str]]:
    """What a stored or submitted value grants, leaving out what is not grants."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        str(name): {str(one) for one in permissions}
        for name, permissions in value.items()
        if isinstance(permissions, list)
    }


@dataclass(eq=False, repr=False)
class PermissionsField(OwnWords, JSONField):
    """What a role grants, as a grid of boxes: a row per view, a column per permission.

    The grid is drawn from the admin's views and pages as they are, so a new
    view gets its row without anyone touching the roles. The form keeps one
    hidden input up to date with the JSON the field reads.
    """

    admin: "Admin | None" = field(default=None, kw_only=True)

    widget = "permissions"

    def grid(self, value: Any) -> Grid:
        """The grid for a stored or submitted value, in the reader's language."""
        granted = _read_grants(value)
        columns = self._columns()
        groups: dict[str, list[GridLine]] = {}
        for view in self._views():
            held = granted.get(view.name, set())
            offered = _offered(view)
            boxes = [
                GridBox(permission, label, permission in held)
                if permission in offered
                else None
                for permission, label in columns
            ]
            actions = [
                GridBox(permission, label, permission in held)
                for permission, label in _asked_by_actions(view)
            ]
            groups.setdefault(view.group, []).append(
                GridLine(view.name, view.label_plural, boxes, actions)
            )
        laid_out = [GridGroup(label, lines) for label, lines in groups.items()]
        pages = [_page_line(page, granted, columns) for page in self._pages()]
        if pages:
            laid_out.append(GridGroup(_("Pages"), pages))
        kept = {
            line.name: [
                box.permission
                for box in [*line.boxes, *line.actions]
                if box is not None and box.checked
            ]
            for group in laid_out
            for line in group.lines
        }
        return Grid(
            columns, laid_out, {name: held for name, held in kept.items() if held}
        )

    def parse(self, raw: str | None) -> Any:
        """Read the JSON the grid sends: nothing ticked grants nothing."""
        if raw is None or not raw.strip():
            return {}
        return super().parse(raw)

    def check(self, value: Any, record: Any = None) -> Any:
        """Keep what the admin's views and pages offer, with View on every row.

        A grant for a view that is gone, or for a permission it no longer
        offers, is dropped.
        """
        if value is None:
            return {}
        if not _grants_shaped(value):
            raise FieldValidationError(
                self.name,
                _(
                    "Write the permissions as JSON, each view with a list of "
                    "the permissions it grants."
                ),
            )
        offered = self._offered_by_name()
        kept: dict[str, list[str]] = {}
        for name, permissions in value.items():
            wanted = set(permissions) & offered.get(name, set())
            if wanted:
                kept[name] = sorted(wanted | {Permission.VIEW.value})
        return kept

    def display(self, value: Any) -> str:
        """What is granted, view by view: "Orders: View, Edit"."""
        grid = self.grid(value)
        written = []
        for group in grid.groups:
            for line in group.lines:
                named = [
                    box.label
                    for box in [*line.boxes, *line.actions]
                    if box is not None and box.checked
                ]
                if named:
                    written.append(f"{line.label}: {_(', ').join(named)}")
        return " · ".join(written)

    def _columns(self) -> list[tuple[str, str]]:
        """The permissions every view has a column for, each with its heading."""
        found = [
            (Permission.VIEW.value, _("View")),
            (Permission.CREATE.value, _("Add")),
            (Permission.EDIT.value, _("Edit")),
            (Permission.DELETE.value, _("Delete")),
            (Permission.EXPORT.value, _("Export")),
            (Permission.IMPORT.value, _("Import")),
        ]
        # Without an audit log there is no history to keep from anyone.
        if self.admin is not None and self.admin.audit is not None:
            found.append((Permission.HISTORY.value, _("History")))
        return found

    def _views(self) -> "list[ModelView[Any]]":
        """The views a role can grant, which is all but the Users and Roles."""
        if self.admin is None:
            return []
        return [view for view in self.admin.views if view.name not in OWN_VIEWS]

    def _pages(self) -> list[Any]:
        """The pages of your own a role can grant, leaving out those for everyone."""
        if self.admin is None:
            return []
        return [
            page
            for page in self.admin.pages.values()
            if not getattr(page, "for_everyone", False)
        ]

    def _offered_by_name(self) -> dict[str, set[str]]:
        """Every permission each view and page offers, by its name in the grants."""
        found = {
            view.name: _offered(view)
            | {permission for permission, _label in _asked_by_actions(view)}
            for view in self._views()
        }
        for page in self._pages():
            found[f"-/{page.name}"] = {Permission.VIEW.value}
        if self.admin is None or self.admin.audit is None:
            for permissions in found.values():
                permissions.discard(Permission.HISTORY.value)
        return found


def _page_line(
    page: Any, granted: Mapping[str, set[str]], columns: list[tuple[str, str]]
) -> GridLine:
    """A page of your own, which has a box under View alone."""
    name = f"-/{page.name}"
    held = granted.get(name, set())
    boxes = [
        GridBox(permission, label, permission in held)
        if permission == Permission.VIEW
        else None
        for permission, label in columns
    ]
    return GridLine(name, page.label, boxes, [])


def _offered(view: "ModelView[Any]") -> set[str]:
    """The standard permissions a view's own settings leave on."""
    return {
        permission
        for permission in STANDARD
        if permission != Permission.VIEW_DETAIL and view._switched_on(permission)
    }


def _asked_by_actions(view: "ModelView[Any]") -> list[tuple[str, str]]:
    """The permissions a view's actions ask for beyond the columns, each labelled.

    Actions that ask for the same one share its box, named after them all.
    """
    asked: dict[str, list[str]] = {}
    for found in view._actions.marked.values():
        if found.permission not in STANDARD:
            asked.setdefault(found.permission, []).append(found.label)
    return [(permission, _(", ").join(labels)) for permission, labels in asked.items()]


def _grants_shaped(value: Any) -> bool:
    """Whether a value maps names to lists of permission names."""
    return isinstance(value, Mapping) and all(
        isinstance(name, str)
        and isinstance(permissions, list)
        and all(isinstance(one, str) for one in permissions)
        for name, permissions in value.items()
    )
