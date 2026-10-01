"""What a view's saves, deletes and actions write to the audit log."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect as sqlalchemy_inspect

from adminsite.audit.actor import actor_of
from adminsite.audit.entry import AuditEntry, AuditEvent, Change, diff
from adminsite.audit.inputs import HIDDEN
from adminsite.audit.store import record_or_warn
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import AdminSiteError
from adminsite.i18n import gettext as _
from adminsite.views.view_fields import ViewFields

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["AuditRecorder"]


class AuditRecorder:
    """The entries a view writes to the audit log, saved with the change or after it."""

    def __init__(self, view: "ModelView[Any]", fields: ViewFields) -> None:
        self._view = view
        self._fields = fields

    def snapshot(self, record: Any, paths: Sequence[str]) -> dict[str, Any]:
        """What a record shows for these paths, as the history records it.

        Only what is already loaded is read. Touching anything else would
        start a lazy load, which an async session cannot do.
        """
        state = sqlalchemy_inspect(record, raiseerr=False)
        unloaded = state.unloaded if state is not None else set()
        return {
            path: self._fields.display(record, path)
            for path in paths
            if path.split(".", 1)[0] not in unloaded
            and not self._fields.form_only(path)
        }

    def masked(self, changes: Mapping[str, Change]) -> dict[str, Change]:
        """Changes as the audit log keeps them: a secret's values as ***.

        A field given `secret=True`, or named like `password_hash` or
        `api_key`, is kept as *** before and after, so a change to it
        still shows, and what it holds never does.
        """
        kept = {}
        for path, (before, after) in changes.items():
            if self._fields.secret(path):
                before = HIDDEN if before not in (None, "") else before
                after = HIDDEN if after not in (None, "") else after
            kept[path] = (before, after)
        return kept

    def record_save(
        self,
        session: SessionAdapter,
        record: Any,
        before: Mapping[str, Any],
        paths: Sequence[str],
        request: Any,
        *,
        created: bool,
    ) -> None:
        """Write down a save: what it set on a new record, or what it changed."""
        if self._view._audit_log is None:
            return
        after = self.snapshot(record, paths)
        changes = self.masked(
            {name: (None, value) for name, value in after.items() if value}
            if created
            else diff(before, after)
        )
        if not changes and not created:
            return
        self.write(
            session,
            [
                AuditEntry(
                    view=self._view.name,
                    record_key=self._fields.identity_of(record),
                    record_title=self._view.get_record_title(record),
                    event=AuditEvent.CREATED if created else AuditEvent.UPDATED,
                    changes=changes,
                    **actor_of(request),
                )
            ],
        )

    def record_failure(
        self, session: SessionAdapter, entries: Sequence[AuditEntry], error: Exception
    ) -> None:
        """Write entries down as failed, once the work they describe is undone."""
        log = self._view._audit_log
        if log is None or not entries:
            return
        # A refusal is worded for people; anything else is a fault, and only
        # its kind goes in the log, since its text may hold the data itself.
        reason = (
            str(error)
            if isinstance(error, AdminSiteError)
            else _("It stopped with an error: {kind}.", kind=type(error).__name__)
        )
        failed = [replace(entry, error=reason) for entry in entries]

        async def write() -> None:
            await record_or_warn(log, failed)

        session.after_rollback(write)

    def write(self, session: SessionAdapter, entries: Sequence[AuditEntry]) -> None:
        """Write entries down with the change they describe.

        A log in the admin's own database is written in the same transaction,
        so the change and its entries are saved together or not at all. Any
        other log is written once the change has committed; if that fails,
        the change stays, and the server log says which entries were lost.
        """
        log = self._view._audit_log
        if log is None or not entries:
            return
        within = getattr(log, "record_within", None)
        if self._view._audit_with_changes and within is not None:

            async def write_within() -> None:
                await within(session, entries)

            session.before_commit(write_within)
            return

        async def write() -> None:
            await record_or_warn(log, entries)

        session.after_commit(write)
