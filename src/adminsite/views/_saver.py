"""A view's saves and deletes, each in one transaction with its hooks."""

from collections.abc import Mapping, Sequence
from functools import partial
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from sqlalchemy import inspect as sqlalchemy_inspect
from starlette.requests import Request

from adminsite._orm.repository import SQLAlchemyRepository
from adminsite.actions.selection import Selection
from adminsite.audit._actor import actor_of
from adminsite.audit.entry import AuditEntry, AuditEvent
from adminsite.database import SessionAdapter
from adminsite.exceptions import (
    IntegrityError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
)
from adminsite.fields import RelationField
from adminsite.fields.files import FileField, NewFile
from adminsite.i18n import format_number, in_sentence, ngettext
from adminsite.i18n import gettext as _
from adminsite.inspector import SQLAlchemyInspector
from adminsite.permissions import Permission, RequestAction
from adminsite.views._audit import AuditRecorder
from adminsite.views._fields import ViewFields
from adminsite.views._forms import InlineRow
from adminsite.views._links import Links
from adminsite.views._pages import PageFields
from adminsite.views.contexts import (
    DeleteContext,
    SaveContext,
    SaveValues,
    stored_values,
)

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = [
    "BULK_DELETE_LIMIT",
    "Saver",
]

# The model of the view this part belongs to.
M = TypeVar("M")

# The most records one Delete removes, each loaded and run through the
# hooks inside a single transaction.
BULK_DELETE_LIMIT = 1000


class Saver(Generic[M]):
    """A view's saves and deletes, each in one transaction with its hooks."""

    def __init__(
        self,
        view: "ModelView[M]",
        fields: ViewFields[M],
        pages: PageFields[M],
        links: Links,
        audit: AuditRecorder[M],
        repository: SQLAlchemyRepository[M],
        inspector: SQLAlchemyInspector,
    ) -> None:
        self._view = view
        self._model: type[M] = view.model
        self._fields = fields
        self._pages = pages
        self._links = links
        self._audit = audit
        self._repository = repository
        self._inspector = inspector

    async def save(
        self,
        session: SessionAdapter,
        values: Mapping[str, Any],
        *,
        record: M | None = None,
        request: Request,
        inline_rows: Mapping[str, Sequence[InlineRow]] | None = None,
    ) -> M:
        """Create or change a record, running the hooks in one transaction.

        A hook that raises rolls the whole save back, so business rules can
        refuse a change.
        """
        created = record is None
        await self._view._ensure(
            Permission.CREATE if created else Permission.EDIT,
            request=request,
            record=record,
        )
        # A copy, so a hook can change what is stored without the caller's
        # own dictionary changing under it.
        values, stored = await self._store_files(session, dict(values), record)
        try:
            async with session.transaction():
                values = await self._links.resolve(session, values, request)
                target = record if record is not None else self._repository.model()
                context = SaveContext(
                    session=session,
                    record=target,
                    values=SaveValues(target, values, form_only=self._fields.form_only),
                    created=created,
                    request=request,
                )
                await self._view.before_save(context)
                # Whatever the hook left in context.values is what is stored,
                # apart from form-only values, which the hooks store themselves.
                values = {
                    path: value
                    for path, value in stored_values(context.values).items()
                    if not self._fields.form_only(path)
                }

                auditing = self._view._audit_log is not None
                before = (
                    self._audit.snapshot(target, list(values))
                    if auditing and not created
                    else {}
                )
                if not created:
                    await self._clear_reordered(session, target, values)
                async with session.no_autoflush():
                    await self._repository.apply_values(session, target, values)
                    await self._apply_inlines(
                        session, target, inline_rows or {}, request
                    )
                if created:
                    await session.add(target)
                await _flush_and_reload(session, target)

                await self._view.after_save(context)
                # A change the hook made to the record is flushed here rather
                # than at the commit, so what that flush sets is read too.
                await _flush_and_reload(session, target)
                session.after_commit(partial(self._view.after_save_committed, context))
                self._audit.record_save(
                    session, target, before, list(values), request, created=created
                )
        except IntegrityError as error:
            await self._discard_files(stored)
            raise RefusedError(
                _(
                    "This {thing} could not be saved. {reason}",
                    thing=in_sentence(self._view.label),
                    reason=str(error),
                )
            ) from error
        except BaseException:
            await self._discard_files(stored)
            raise
        return target

    async def _clear_reordered(
        self, session: SessionAdapter, record: M, values: Mapping[str, Any]
    ) -> None:
        """Empty each ordered link whose new order a plain save would lose.

        Saving a link to many only adds and removes what changed, so the rows
        kept stay where they were, and one added goes last. That loses a new
        order, so such a link is emptied here, and written again whole, in
        the order given, when the values are applied.
        """
        cleared = False
        for path, value in values.items():
            item = self._fields.field_for(path)
            if not isinstance(item, RelationField) or not item.ordered:
                continue
            target = SQLAlchemyRepository(item.related_model, self._inspector)

            def key_of(one: Any, target: SQLAlchemyRepository[Any] = target) -> str:
                return (
                    target.identity_of(one)
                    if isinstance(one, target.model)
                    else str(one)
                )

            held = [key_of(one) for one in getattr(record, path) or ()]
            wanted = [key_of(one) for one in value or ()]
            # What adding and removing alone would leave: the rows kept, in
            # their old order, then the new ones.
            kept = [key for key in held if key in wanted]
            if kept + [key for key in wanted if key not in held] != wanted:
                setattr(record, path, [])
                cleared = True
        if cleared:
            await session.flush()

    async def _store_files(
        self, session: SessionAdapter, values: Mapping[str, Any], record: M | None
    ) -> tuple[dict[str, Any], list[tuple[FileField, str]]]:
        """Store new uploads and swap them for their keys.

        The files a save replaces or removes are deleted once it commits,
        so a save that fails never loses the file that was there.
        """
        ready = dict(values)
        stored: list[tuple[FileField, str]] = []
        try:
            for path, value in values.items():
                item = self._fields.field_for(path)
                if not isinstance(item, FileField):
                    continue
                if isinstance(value, NewFile):
                    key = await item.storage.save(value.upload)
                    stored.append((item, key))
                    ready[path] = key
                old = (
                    self._fields.value_at(record, path) if record is not None else None
                )
                if old and old != ready[path]:
                    session.after_commit(_deleting(item, old))
        except BaseException:
            await self._discard_files(stored)
            raise
        return ready, stored

    async def _discard_files(self, stored: Sequence[tuple[FileField, str]]) -> None:
        """Remove files stored for a save that did not go through."""
        for item, key in stored:
            await item.storage.delete(key)

    async def _apply_inlines(
        self,
        session: SessionAdapter,
        parent: M,
        inline_rows: Mapping[str, Sequence[InlineRow]],
        request: Request,
    ) -> None:
        """Add, change and remove child records as the form asked."""
        for inline in self._view.inlines:
            rows = inline_rows.get(inline.name)
            if not rows:
                continue
            child_view = self._view._inline_views[inline.name]
            children = getattr(parent, inline.name)
            by_key = {
                child_view._fields.identity_of(child): child for child in children
            }
            for row in rows:
                if row.is_new:
                    if not row.delete:
                        child = child_view.model()
                        values = await self._links.row_values(
                            session, inline, row, request
                        )
                        await child_view._repository.apply_values(
                            session, child, values
                        )
                        children.append(child)
                    continue
                existing = by_key.get(row.key)
                if existing is None:
                    raise RecordNotFoundError(child_view.model, row.key)
                if row.delete:
                    if inline.can_delete:
                        children.remove(existing)
                        await session.delete(existing)
                    continue
                values = await self._links.row_values(session, inline, row, request)
                await child_view._repository.apply_values(session, existing, values)

    async def delete(
        self, session: SessionAdapter, record: M, *, request: Request
    ) -> None:
        """Delete a record, running the hooks in one transaction."""
        await self._view._ensure(Permission.DELETE, request=request, record=record)
        try:
            async with session.transaction():
                await self._delete_within(session, record, request=request)
        except _ReferredToError as error:
            raise RefusedError(
                _(
                    "This {thing} cannot be deleted, because other records "
                    "still refer to it.",
                    thing=in_sentence(self._view.label),
                )
            ) from error
        except IntegrityError as error:
            # Refused for something else, such as a row a hook wrote.
            raise RefusedError(
                _(
                    "This {thing} could not be deleted. {reason}",
                    thing=in_sentence(self._view.label),
                    reason=str(error),
                )
            ) from error

    async def _delete_within(
        self, session: SessionAdapter, record: M, *, request: Request
    ) -> None:
        """Delete one record inside a transaction the caller holds open."""
        await self._view._ensure(Permission.DELETE, request=request, record=record)
        context = DeleteContext(session=session, record=record, request=request)
        await self._view.before_delete(context)
        auditing = self._view._audit_log is not None
        before = (
            self._audit.snapshot(record, self._pages.form_fields(request, record))
            if auditing
            else {}
        )
        key, title = (
            self._fields.identity_of(record),
            self._view.get_record_title(record),
        )
        # What the hooks left unwritten goes first, so that a refusal of the
        # delete itself is the only one put down to the records referring.
        await session.flush()
        try:
            await self._repository.delete(session, record)
        except IntegrityError as error:
            raise _ReferredToError(str(error)) from error
        await self._view.after_delete(context)
        session.after_commit(partial(self._view.after_delete_committed, context))
        if not auditing:
            return
        self._audit.write(
            session,
            [
                AuditEntry(
                    view=self._view.name,
                    record_key=key,
                    record_title=title,
                    event=AuditEvent.DELETED,
                    changes=self._audit.masked(
                        {
                            name: (value, None)
                            for name, value in before.items()
                            if value not in ("", None)
                        }
                    ),
                    **actor_of(request),
                )
            ],
        )

    async def delete_selected(self, selection: Selection[M]) -> str:
        """Delete the chosen records, each as a single delete would, all or none.

        Every record goes through `allows`, `before_delete` and
        `after_delete`, and gets its own entry in the audit log. When one is
        refused, nothing is deleted, and the message names it.
        """
        request = selection.request
        records = await selection.records(
            paths=self._pages.loadable(
                self._pages.form_fields(request, page=RequestAction.EDIT)
            )
        )
        if len(records) > BULK_DELETE_LIMIT:
            raise RefusedError(
                _(
                    "Delete at most {count} at a time. Narrow the list first.",
                    count=format_number(BULK_DELETE_LIMIT),
                )
            )
        for record in records:
            title = self._view.get_record_title(record)
            # The database refusing anything but the delete itself, such as a
            # row a hook wrote, goes on to the action's own handling, which
            # says Delete was not done and why, as it does at the commit.
            try:
                await self._delete_within(selection.session, record, request=request)
            except (RefusedError, PermissionDeniedError) as error:
                raise RefusedError(
                    _(
                        "Nothing was deleted, because {thing} cannot be: {reason}",
                        thing=title,
                        reason=str(error),
                    )
                ) from error
            except _ReferredToError as error:
                raise RefusedError(
                    _(
                        "Nothing was deleted, because other records still refer "
                        "to {thing}.",
                        thing=title,
                    )
                ) from error
        return ngettext(
            "{count} {thing} deleted.",
            "{count} {things} deleted.",
            len(records),
            thing=in_sentence(self._view.label),
            things=in_sentence(self._view.label_plural),
        )


def _deleting(item: FileField, key: str) -> Any:
    """Work that deletes one stored file, for after the commit."""

    async def work() -> None:
        await item.storage.delete(key)

    return work


async def _flush_and_reload(session: SessionAdapter, record: Any) -> None:
    """Flush, then read back what the database set, such as an onupdate time."""
    await session.flush()
    # Read while the transaction can still load it, so the hooks and the
    # pages after the commit can read it too.
    expired = sqlalchemy_inspect(record, raiseerr=True).expired_attributes
    if expired:
        await session.refresh(record, sorted(expired))


class _ReferredToError(IntegrityError):
    """The database refused a delete, since other records refer to the record."""
