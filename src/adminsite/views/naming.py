"""How a record that another record links to is named, the same everywhere."""

from typing import TYPE_CHECKING, Any

from adminsite.fields import RelationField
from adminsite.i18n import gettext as _
from adminsite.protocols import ModelInspector
from adminsite.text import names_itself

if TYPE_CHECKING:
    from adminsite.views.registry import ViewRegistry


def name_linked(
    item: RelationField,
    record: Any,
    *,
    views: "ViewRegistry | None",
    inspector: ModelInspector,
) -> str:
    """Name a record a link points at, the same way wherever it shows.

    The link's own `display_template` comes first. Then the view that shows
    the other model names it, as it does on its own pages. Without one, the
    model's own `__str__`, or else its name and key: "Customer #3".
    """
    if record is None:
        return ""
    if item.display_template is not None:
        return item.label_for(record)
    target = views.for_relation(item) if views is not None else None
    if target is not None:
        return target.title_of(record)
    if names_itself(record):
        return str(record)
    schema = inspector.inspect(item.target)
    key = ",".join(str(getattr(record, name)) for name in schema.primary_key)
    return _("{thing} #{key}", thing=schema.label, key=key)


def name_all_linked(
    item: RelationField,
    value: Any,
    *,
    views: "ViewRegistry | None",
    inspector: ModelInspector,
) -> str:
    """Name what a link holds: one record, or each of several."""
    if value is None:
        return ""
    if isinstance(value, list | tuple | set):
        return ", ".join(
            name_linked(item, one, views=views, inspector=inspector) for one in value
        )
    return name_linked(item, value, views=views, inspector=inspector)
