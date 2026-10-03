"""A submitted form, its inline rows and an action's inputs, read into values."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeGuard

from sqlalchemy.orm import class_mapper

from adminsite.actions.action import Action
from adminsite.exceptions import FieldValidationError
from adminsite.fields import BaseField, EnumField, JSONField, RelationField
from adminsite.fields.documents import DocumentError
from adminsite.fields.files import UNCHANGED, FileField, UploadField
from adminsite.views.inline import Inline, InlineRow
from adminsite.views.pages import PageFields
from adminsite.views.view_fields import ViewFields
from adminsite.views.writing import FormData, FormResult

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["FormParser"]


class FormParser:
    """A view's forms read back into values, each checked as its field says."""

    def __init__(
        self, view: "ModelView[Any]", fields: ViewFields, pages: PageFields
    ) -> None:
        self._view = view
        self._model: type[Any] = view.model
        self._fields = fields
        self._pages = pages

    def parse(
        self,
        data: FormData,
        *,
        record: Any = None,
        request: Any = None,
    ) -> FormResult:
        """Read a submitted form into values, collecting any messages."""
        result = FormResult()
        readonly = set(self._pages.readonly_paths(request, record))
        draft: Any = None

        for path in self._pages.form_fields(request, record):
            item = self._fields.field_for(path)
            if path in readonly:
                continue
            raw = data.get(path)
            if item.keeps_value_when_blank and record is not None and _is_blank(raw):
                # Left empty on a record that exists: it keeps what it has.
                continue
            try:
                if isinstance(item, FileField):
                    choice = item.parse_upload(
                        raw,
                        remove=data.get(f"{path}-remove") is not None,
                        has_file=bool(
                            record is not None and self._fields.value_at(record, path)
                        ),
                    )
                    if choice is not UNCHANGED:
                        result.values[path] = choice
                elif isinstance(item, JSONField) and item.schema is not None:
                    # Drawn from its schema, the document has an input for
                    # each of its parts. A new record's schema may follow
                    # what the rest of the form holds.
                    owner = record
                    if owner is None and item.schema_from_record:
                        if draft is None:
                            draft = self.draft_record(data, request)
                        owner = draft
                    result.values[path] = item.read_form(data, path, record=owner)
                elif _holds_many(item):
                    result.values[path] = item.parse_many(_as_list(raw))
                else:
                    result.values[path] = item.parse(_as_text(raw))
            except DocumentError as error:
                result.errors.update(error.errors)
            except FieldValidationError as error:
                result.errors[path] = error.message

        for inline in self._view.get_inlines(request, record):
            result.inline_rows[inline.name] = self._parse_inline(
                inline, data, result.errors, request, record
            )
        return result

    def _parse_inline(
        self,
        inline: Inline,
        data: FormData,
        errors: dict[str, str],
        request: Any,
        record: Any = None,
    ) -> list[InlineRow]:
        """Read one inline's rows back from the form.

        A new row reads the create page's fields and an existing child the
        edit page's, less those the child or this view locks, as a view's
        form does.
        """
        child = self._view._inline_views[inline.name]
        locked = self._pages.inline_readonly(inline, request, record)
        children = {
            child._fields.identity_of(found): found
            for found in getattr(record, inline.name, None) or ()
        }
        try:
            count = int(_as_text(data.get(f"{inline.name}-count")) or 0)
        except ValueError:
            count = 0

        rows: list[InlineRow] = []
        for index in range(count):
            key = (_as_text(data.get(inline.input_name(index, "key"))) or "").strip()
            delete = data.get(inline.input_name(index, "delete")) is not None
            found = children.get(key) if key else None
            readonly = locked | set(child._pages.readonly_paths(request, found))
            paths = [
                path
                for path in child._pages.form_fields(request, found)
                if path not in readonly
            ]
            raw = {path: data.get(inline.input_name(index, path)) for path in paths}
            if not key and not any(_as_text(value) for value in raw.values()):
                # An empty row left over from "add another".
                continue
            row = InlineRow(key=key, delete=delete, index=index)
            if not delete:
                for path in paths:
                    item = child._fields.field_for(path)
                    try:
                        row.values[path] = item.parse(_as_text(raw[path]))
                    except FieldValidationError as error:
                        errors[inline.input_name(index, path)] = error.message
            rows.append(row)
        return rows

    def draft_record(self, data: FormData, request: Any = None) -> Any:
        """An unsaved record holding the plain values a form holds so far.

        A JSON field whose schema comes from the record is given this while
        the record is new, so the schema can follow what is chosen in the
        form, such as a setting's key. Only the record's own columns are
        set, from the values that can be read; it is never added to a
        session.
        """
        # Made without the model's own __init__, which may ask for values.
        draft = class_mapper(self._model).class_manager.new_instance()
        for path in self._pages.form_fields(request):
            item = self._fields.field_for(path)
            if "." in path or not item.stored:
                continue
            if isinstance(item, RelationField | FileField | JSONField):
                continue
            raw = data.get(path)
            try:
                if _holds_many(item):
                    value = item.parse_many(_as_list(raw))
                else:
                    value = item.parse(_as_text(raw))
            except FieldValidationError:
                continue
            if value is not None:
                setattr(draft, path, value)
        return draft

    def parse_action_inputs(self, found: Action, data: FormData) -> FormResult:
        """Read the values an action asked for, checked like form fields."""
        result = FormResult()
        for item in found.inputs:
            raw = data.get(item.name)
            try:
                if isinstance(item, UploadField):
                    result.values[item.name] = item.read_upload(raw)
                elif _holds_many(item):
                    result.values[item.name] = item.parse_many(_as_list(raw))
                else:
                    result.values[item.name] = item.parse(_as_text(raw))
            except FieldValidationError as error:
                result.errors[item.name] = error.message
        return result


def _as_text(raw: str | Sequence[str] | None) -> str | None:
    """Read one value out of form data, which may hold several."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return raw[0] if raw else None


def _is_blank(raw: str | Sequence[str] | None) -> bool:
    """Whether nothing but spaces was sent."""
    return not (_as_text(raw) or "").strip()


def _holds_many(item: BaseField) -> TypeGuard[RelationField | EnumField]:
    """Whether the input sends several values rather than one."""
    if isinstance(item, RelationField):
        return item.collection
    return isinstance(item, EnumField) and item.multiple


def _as_list(raw: str | Sequence[str] | None) -> list[str]:
    """Read every value out of form data."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)
