"""The keys sent for a view's links, turned into the records they name."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import InvalidPathError, PermissionDeniedError, RefusedError
from adminsite.fields import RelationField
from adminsite.i18n import gettext as _
from adminsite.views.inline import Inline, InlineRow

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["Links"]


class Links:
    """The records a view's links name, read through the linked model's view."""

    def __init__(self, view: "ModelView[Any]", inspector: SQLAlchemyInspector) -> None:
        self._view = view
        self._inspector = inspector

    async def resolve(
        self,
        session: SessionAdapter,
        values: Mapping[str, Any],
        request: Any,
        *,
        fields_of: "ModelView[Any] | None" = None,
    ) -> dict[str, Any]:
        """Turn the keys sent for links into records, through their own view.

        The picker offered only the records the target's view lets this
        user see, so a key for any other record did not come from the form.
        It is refused like any other bad choice, and nothing is said about
        whether the record exists. A target with no view is loaded by key,
        so a hook reads a record either way, never a key.
        """
        owner = fields_of or self._view
        resolved = dict(values)
        for path, value in values.items():
            item = owner._fields.field_for(path)
            if not isinstance(item, RelationField):
                continue
            if value is None or value == "" or value == []:
                continue
            target = (
                self._view._views.for_relation(item)
                if self._view._views is not None
                else None
            )
            if target is None:
                resolved[path] = await owner._repository.linked(session, path, value)
                continue
            keys = value if isinstance(value, list | tuple | set) else [value]
            found = []
            for key in keys:
                record = key
                if not isinstance(record, item.related_model):
                    record = await self.linked_through(target, session, key, request)
                if record is None:
                    raise RefusedError(_("Choose a record."), field=path)
                found.append(record)
            resolved[path] = found if item.collection else found[0]
        return resolved

    async def row_values(
        self, session: SessionAdapter, inline: Inline, row: InlineRow, request: Any
    ) -> dict[str, Any]:
        """A child row's values with the records its links name.

        A refusal names the row's own input, so it is shown in that cell.
        """
        try:
            return await self.resolve(
                session,
                row.values,
                request,
                fields_of=self._view._inline_views[inline.name],
            )
        except RefusedError as error:
            if not error.field or row.index is None:
                raise
            raise RefusedError(
                str(error), field=inline.input_name(row.index, error.field)
            ) from error

    async def linked_through(
        self, target: "ModelView[Any]", session: SessionAdapter, key: Any, request: Any
    ) -> Any | None:
        """One linked record, if the target's view lets this user see it."""
        wanted = key
        if isinstance(key, str) and len(target._schema.primary_key) > 1:
            wanted = tuple(key.split(","))
        try:
            return await target._reader.fetch_record(session, wanted, request=request)
        except (PermissionDeniedError, InvalidPathError):
            return None

    async def linked_directly(
        self, item: RelationField, session: SessionAdapter, key: Any
    ) -> Any | None:
        """A linked record by its key, for a model no view shows."""
        repository = SQLAlchemyRepository(item.related_model, self._inspector)
        wanted = key
        if isinstance(key, str) and len(repository.schema.primary_key) > 1:
            wanted = tuple(key.split(","))
        try:
            return await repository.get(session, wanted)
        except InvalidPathError:
            return None
