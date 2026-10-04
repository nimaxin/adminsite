from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from adminsite._orm.repository import SQLAlchemyRepository
from adminsite.database import SessionAdapter
from adminsite.exceptions import PermissionDeniedError
from adminsite.fields import RelationField
from adminsite.inspector import SQLAlchemyInspector
from adminsite.query import CountMode, Page, QuerySpec
from adminsite.text import template_names
from adminsite.views.model_view import ModelView

if TYPE_CHECKING:
    from adminsite.views.registry import ViewRegistry

__all__ = [
    "PICKER_LIMIT",
    "RESULT_LIMIT",
    "Picker",
]

# Above this many records a relation is searched rather than listed.
PICKER_LIMIT = 100

# How many results a search hands back at a time.
RESULT_LIMIT = 20


@dataclass
class Picker:
    """The records a link may offer, read through the target's own view.

    A link points at another model, and that model usually has a view of
    its own saying who may see its records and which of them. The picker
    reads through that view, so a link can never show what the view
    would not. Where the target has no view there is nothing to ask, and
    the model is read directly.
    """

    views: "ViewRegistry"
    inspector: SQLAlchemyInspector
    item: RelationField
    request: Request

    @property
    def view(self) -> ModelView[Any] | None:
        """The view registered for the model this link points at."""
        return self.views.for_relation(self.item)

    def key_of(self, record: Any) -> str:
        """A linked record's key, as a form sends it back."""
        return self.inspector.inspect(self.item.related_model).identity_of(record)

    def keys_of(self, current: Any) -> list[str]:
        """The keys of what the link holds, however many records that is.

        After a failed submit the values are the keys that were picked, not
        records, so they are already what the picker needs.
        """
        if current is None or current == "":
            return []
        found = current if isinstance(current, list | tuple | set) else [current]
        model = self.item.related_model
        return [
            self.key_of(one) if isinstance(one, model) else str(one) for one in found
        ]

    def _repository(self) -> SQLAlchemyRepository[Any]:
        """The target model's own repository, for a model no view shows."""
        return SQLAlchemyRepository(self.item.related_model, self.inspector)

    def search_paths(self) -> tuple[str, ...]:
        """Where a search looks: what the target view says, or the names.

        A picker used to search every text column of the other model,
        which let a column the view keeps off its pages be read a letter
        at a time. It now searches the target view's own search fields,
        and where it names none, the columns the records are named by,
        which the picker is already showing.
        """
        view = self.view
        if view is not None:
            named = view._pages.search_paths(self.request)
            if named:
                return named
        return self._named_in_label()

    def _named_in_label(self) -> tuple[str, ...]:
        """The text columns the picker's own labels are built from."""
        view = self.view
        # Named by the link's own template first, as everywhere else.
        template = self.item.record_title or (
            view.record_title if view is not None else ""
        )
        if not template:
            return ()
        fields = self.inspector.inspect(self.item.related_model).fields
        return tuple(
            name
            for name in template_names(template)
            if name in fields and fields[name].python_type is str
        )

    async def page(
        self, session: SessionAdapter, *, search: str = "", limit: int = PICKER_LIMIT
    ) -> Page:
        """One page of the records on offer, or raise if none are."""
        view = self.view
        term = search.strip()
        spec = QuerySpec(
            search=search,
            search_paths=self.search_paths(),
            # The target view decides how its records are searched, here too.
            search_condition=view.search_condition(term, request=self.request)
            if view is not None and term
            else None,
            limit=limit,
            count=CountMode.NONE,
        )
        if view is None:
            return await self._repository().list(session, spec)
        return await view._reader.fetch_page(session, spec, request=self.request)

    async def offered(
        self, session: SessionAdapter, *, search: str = "", limit: int = PICKER_LIMIT
    ) -> Page | None:
        """The same page, or nothing where the user may see none of it.

        A form whose link points at a view this user may not open shows an
        empty picker rather than refusing the whole page.
        """
        try:
            return await self.page(session, search=search, limit=limit)
        except PermissionDeniedError:
            return None

    async def get(self, session: SessionAdapter, key: str) -> Any | None:
        """One record the link already holds, if the user may see it."""
        view = self.view
        if view is None:
            return await self._repository().get(session, key)
        try:
            return await view._reader.fetch_record(session, key, request=self.request)
        except PermissionDeniedError:
            return None
