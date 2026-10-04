"""The fields a view shows: the one for each path, and how it reads a record."""

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from sqlalchemy import inspect as sqlalchemy_inspect

from adminsite._orm.repository import SQLAlchemyRepository
from adminsite.audit.inputs import looks_secret
from adminsite.columns import path_of
from adminsite.exceptions import AdminSiteError
from adminsite.fields import (
    BaseField,
    ComputedField,
    Field,
    FieldRegistry,
    RelationField,
)
from adminsite.i18n import gettext as _
from adminsite.inspector import SQLAlchemyInspector
from adminsite.security import RequestAction
from adminsite.views.checks import (
    check_kind,
    check_link_title,
    check_list_flags,
    check_options_used,
)
from adminsite.views.naming import HIDDEN, is_unseen, name_all_linked, name_linked
from adminsite.views.settings import SettingsReader

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["ViewFields"]

# The model of the view this part belongs to.
M = TypeVar("M")


class ViewFields(Generic[M]):
    """The field for each path a view shows, and how it reads a record.

    A field is built from its column when first asked for, with the options
    the view gave it.
    """

    def __init__(
        self,
        view: "ModelView[M]",
        settings: SettingsReader,
        inspector: SQLAlchemyInspector,
        registry: FieldRegistry,
        repository: SQLAlchemyRepository[M],
    ) -> None:
        self._view = view
        self._model: type[M] = view.model
        self._settings = settings
        self._inspector = inspector
        self._registry = registry
        self._repository = repository
        self._built: dict[str, BaseField] = {}
        # Built now, so a mistake such as a tone for a value the field does
        # not have stops the admin starting rather than the page that shows it.
        for path in settings.placed:
            try:
                item = self.field_for(path)
                check_list_flags(
                    item,
                    settings.overrides.get(path, item),
                    excluded_by_list=path in settings.excluded[RequestAction.LIST],
                )
            except AdminSiteError as error:
                raise AdminSiteError(f"{type(view).__name__}.fields: {error}") from None

    def field_for(self, path: str) -> BaseField:
        """The field used to show and edit whatever the path points at."""
        known = self._built.get(path)
        if known is not None:
            return known
        given = self._settings.overrides.get(path)
        built = self._built_field(path) if given is None else self._completed(given)
        self._built[path] = built
        return built

    def _completed(self, given: BaseField) -> BaseField:
        """A field from `fields`, with what its column says for options left out.

        A computed or form-only field has no column, so it is used as it is.
        `Field(...)` on its own becomes the field adminsite picks for the
        column, with the options it was given. A kind that does not fit its
        column, or an option the kind never reads, is refused.
        """
        if not isinstance(given, Field) or given.form_only:
            check_options_used(given, given)
            given.check_options()
            return given
        path = path_of(given.column, self._model)
        resolved = self._inspector.resolve(self._model, path)
        completed: BaseField
        # Built under the path, so a column of a related model is named
        # customer.email rather than email.
        if type(given) is Field:
            if resolved.field is not None:
                completed = self._registry.build(
                    replace(resolved.field, name=path), **given.given_options()
                )
            else:
                completed = RelationField.from_relation(
                    replace(resolved.relations[-1], name=path),
                    **given.given_options(),
                )
        elif resolved.field is not None:
            check_kind(
                given,
                resolved.field,
                resolved,
                self._model,
                registry=self._registry,
                inspector=self._inspector,
            )
            completed = self._registry.fill(given, resolved.field)
        elif isinstance(given, RelationField):
            completed = given.filled_from_relation(resolved.relations[-1])
        else:
            raise AdminSiteError(
                f"{given!r} names a relationship. Show it with RelationField, or "
                "name it in fields without a field."
            )
        check_options_used(given, completed)
        completed.check_options()
        check_link_title(completed, repr(given), self._inspector)
        return completed

    def _built_field(self, path: str) -> BaseField:
        """The field adminsite works out for a path nobody gave a field for."""
        resolved = self._inspector.resolve(self._model, path)
        if resolved.field is not None:
            return self._registry.build(replace(resolved.field, name=path))
        return RelationField.from_relation(replace(resolved.relations[-1], name=path))

    def label_for(self, path: str) -> str:
        """The column heading for a path.

        A path through a link names the link as well, so `customer.name`
        reads Customer name rather than a bare Name.
        """
        item = self.field_for(path)
        label = item.label
        if item.labelled or "." not in path:
            return label
        resolved = self._inspector.resolve(self._model, path)
        if resolved.field is None:
            return label
        owner = resolved.relations[-1].label
        return f"{owner} {label[:1].lower()}{label[1:]}"

    def form_only(self, path: str) -> bool:
        """Whether a path is a form-only field of this view."""
        try:
            return self.field_for(path).form_only
        except AdminSiteError:
            return False

    def secret(self, path: str) -> bool:
        """Whether a path holds a secret, which the audit log keeps as ***."""
        try:
            chosen = self.field_for(path).secret
        except AdminSiteError:
            chosen = None
        return looks_secret(path.rsplit(".", 1)[-1]) if chosen is None else chosen

    def needs_of(self, item: BaseField) -> list[str]:
        """The paths a computed field reads, checked when the view is built."""
        if not isinstance(item, ComputedField):
            return list(getattr(item, "needs", ()))
        return [path_of(needed, self._model) for needed in item.needs]

    def value_at(self, record: M, path: str, *, seen: bool = False) -> Any:
        """Read the value a path points at, following links as it goes.

        Past a link to many it reads a value for each record, in one flat
        list, so orders.items.quantity holds the quantity of every item.

        With `seen`, for what a page shows, a linked record its own view's
        scope keeps from this user is left out of a link to many, and a link
        to one such record reads as HIDDEN.
        """
        value: Any = record
        parts = path.split(".")
        for position, part in enumerate(parts):
            if value is None:
                return None
            if not isinstance(value, list | tuple | set):
                value = getattr(value, part, None)
                if seen and is_unseen(value):
                    return HIDDEN
                if seen and isinstance(value, list | tuple | set):
                    value = [one for one in value if not is_unseen(one)]
                continue
            # A link to many is read through, while a column holding a list is
            # one record's value.
            through = (
                position < len(parts) - 1
                or self._inspector.resolve(self._model, path).points_at_relation
            )
            found: list[Any] = []
            for item in value:
                if seen and is_unseen(item):
                    continue
                one = getattr(item, part, None)
                if through and isinstance(one, list | tuple | set):
                    found.extend(one)
                else:
                    found.append(one)
            value = found
        return value

    def hides(self, record: M, path: str) -> bool:
        """Whether a path reads through a linked record kept from this user.

        Only what the read loaded is looked at, so asking never starts a
        lazy load: a link a page reads on its own, such as a link to many
        named a few records at a time, holds nothing here to hide.
        """
        value: Any = record
        for part in path.split("."):
            if value is None or isinstance(value, list | tuple | set):
                return False
            loaded = getattr(value, "__dict__", {})
            if part not in loaded:
                return False
            value = loaded[part]
            if is_unseen(value):
                return True
        return False

    def display(self, record: M, path: str, *, as_seen: bool = True) -> str:
        """The text shown in a cell.

        A value read through a linked record its view's scope keeps from
        this user reads as Hidden, unless `as_seen` is False, as for the
        audit log, which keeps what is true.
        """
        item = self.field_for(path)
        if item.form_only:
            # Never read from the record, so there is nothing to show.
            return ""
        value = self.value_at(record, path, seen=as_seen)
        if value is HIDDEN:
            return _("Hidden")
        if isinstance(item, RelationField):
            # A linked record reads here as it does everywhere else.
            return name_all_linked(
                item,
                value,
                views=self._view._views,
                inspector=self._inspector,
                as_seen=as_seen,
            )
        if (
            "." in path
            and self._inspector.resolve(self._model, path).crosses_collection
        ):
            # Read through a link to many, it holds a value for each record.
            shown = (item.text_for(record, one) for one in value or ())
            return ", ".join(text for text in shown if text)
        return item.text_for(record, value)

    def name_linked(self, item: RelationField, record: Any) -> str:
        """Name a record one of this view's links points at."""
        return name_linked(
            item, record, views=self._view._views, inspector=self._inspector
        )

    def identity_of(self, record: M) -> str:
        """The key of a record, as it appears in a URL."""
        return self._repository.identity_of(record)

    def key_value(self, record: M) -> Any:
        """A record's primary key as its columns hold it: a tuple when composite."""
        state = sqlalchemy_inspect(record, raiseerr=True)
        identity: tuple[Any, ...] = state.identity or ()
        return identity[0] if len(identity) == 1 else tuple(identity)
