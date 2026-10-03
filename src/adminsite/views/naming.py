"""How a record that another record links to is named, the same everywhere."""

from typing import TYPE_CHECKING, Any

from adminsite.fields import RelationField
from adminsite.i18n import gettext as _
from adminsite.protocols import ModelInspector
from adminsite.text import names_itself

if TYPE_CHECKING:
    from adminsite.views.registry import ViewRegistry

__all__ = [
    "HIDDEN",
    "is_unseen",
    "mark_unseen",
    "name_all_linked",
    "name_linked",
]

# Set on a linked record that its own view's scope keeps from this user, for
# the rest of the request, so its name and values never show through a link.
UNSEEN = "_adminsite_unseen"


class _Hidden:
    """What a path reads as when it passes through a record kept from this user."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "HIDDEN"


HIDDEN = _Hidden()


def mark_unseen(record: Any) -> None:
    """Keep a linked record's name and values from this user."""
    vars(record)[UNSEEN] = True


def is_unseen(value: Any) -> bool:
    """Whether a linked record is kept from this user."""
    return bool(getattr(value, "__dict__", {}).get(UNSEEN, False))


def name_linked(
    item: RelationField,
    record: Any,
    *,
    views: "ViewRegistry | None",
    inspector: ModelInspector,
    as_seen: bool = True,
) -> str:
    """Name a record a link points at, the same way wherever it shows.

    The link's own `record_title` comes first. Then the view that shows
    the other model names it, as it does on its own pages. Without one, the
    model's own `__str__`, or else its name and key: "Customer #3".

    A record its view's scope keeps from this user reads as Hidden, unless
    `as_seen` is False, as for the audit log, which keeps what is true.
    """
    if record is None:
        return ""
    if as_seen and is_unseen(record):
        return _("Hidden")
    if item.record_title is not None:
        return item.label_for(record)
    target = views.for_relation(item) if views is not None else None
    if target is not None:
        return target.get_record_title(record)
    if names_itself(record):
        return str(record)
    schema = inspector.inspect(item.related_model)
    key = ",".join(str(getattr(record, name)) for name in schema.primary_key)
    return _("{thing} #{key}", thing=schema.label, key=key)


def name_all_linked(
    item: RelationField,
    value: Any,
    *,
    views: "ViewRegistry | None",
    inspector: ModelInspector,
    as_seen: bool = True,
) -> str:
    """Name what a link holds: one record, or each of several.

    Of several, those kept from this user are left out, as the record page
    leaves them out of a link to many.
    """
    if value is None:
        return ""
    if isinstance(value, list | tuple | set):
        return ", ".join(
            name_linked(item, one, views=views, inspector=inspector, as_seen=as_seen)
            for one in value
            if not (as_seen and is_unseen(one))
        )
    return name_linked(item, value, views=views, inspector=inspector, as_seen=as_seen)
