"""Setting and method names other admins use, or adminsite did before 0.1.0a10."""

from typing import Any

from adminsite.actions.action import action_of
from adminsite.exceptions import AdminSiteError

__all__ = ["refuse_old_names"]

# Settings and methods under the names other admins give them, or adminsite
# did before 0.1.0a10, with the name adminsite uses. Python would take one as a
# new attribute that adminsite never reads, so a view setting one is refused.
_RENAMED = {
    "list_filter": "list_filters",
    "get_filters": "get_list_filters",
    "page_sizes": "page_size_options",
    "bulk_delete": "can_delete_selected",
    "search_fields": "searchable_fields",
    "get_search_fields": "get_searchable_fields",
    "ordering": "fields_default_sort",
    "get_ordering": "get_fields_default_sort",
    "display_template": "record_title",
    "title_of": "get_record_title",
    "can_detail": "can_view_detail",
    "form_values": "form_only_values",
}

# Settings and methods adminsite has nothing by that name for, as other
# admins do, each with what to write instead.
_REPLACED = {
    "list_display": (
        "List the view's fields in fields, and leave one off the list with "
        "exclude_fields_from_list."
    ),
    "list_columns": "Give each of those fields hidden_in_list=True in fields.",
    "form_fields": (
        "List the view's fields in fields, and leave one off the forms with "
        "exclude_fields_from_create and exclude_fields_from_edit."
    ),
    "detail_fields": (
        "List the view's fields in fields, and leave one off the record page "
        "with exclude_fields_from_detail."
    ),
    "exclude": "List the fields to show in fields, leaving those out.",
    "readonly_fields": (
        "Give each of those fields read_only=True in fields, or name them in "
        "get_readonly_fields."
    ),
    "get_list_display": (
        "Decide who sees a field with can_access_field(request, field, action)."
    ),
    "get_form_fields": (
        "Decide who sees a field with can_access_field(request, field, action)."
    ),
    "get_detail_fields": (
        "Decide who sees a field with can_access_field(request, field, action)."
    ),
    "get_column_choices": (
        "Give a field hidden_in_list=True to offer it in the Columns menu, and "
        "decide who sees one with can_access_field(request, field, action)."
    ),
    "get_page_sizes": (
        "List the sizes in page_size_options; the view's page_size is offered "
        "with them."
    ),
}


def refuse_old_names(view: type[Any], *, base: type[Any], delete_action: str) -> None:
    """Refuse a setting or method written under the name it had before.

    Every class from `view` up to `base` is looked through, so a view
    built on a view of the project's own is checked too. A method named
    `delete_action` does not replace the built-in delete of the chosen
    rows, so it is refused as well.
    """
    name = view.__name__
    for owner in view.__mro__:
        if owner is base:
            return
        written = vars(owner)
        for old, new in _RENAMED.items():
            if old in written:
                verb = "defines" if callable(written[old]) else "sets"
                raise AdminSiteError(
                    f"{name} {verb} {old}, which adminsite calls {new}. "
                    f"Rename it to {new}."
                )
        for old, instead in _REPLACED.items():
            if old not in written:
                continue
            if callable(written[old]):
                said = f"{name} defines {old}, a method adminsite does not call."
            else:
                said = f"{name} sets {old}, a setting adminsite does not have."
            raise AdminSiteError(f"{said} {instead}")
        replaced = written.get(delete_action)
        if replaced is not None and action_of(replaced) is None:
            raise AdminSiteError(
                f"{name}.{delete_action} does not replace the built-in delete "
                "of the chosen rows. Set can_delete_selected = False and add "
                "an action of your own."
            )
