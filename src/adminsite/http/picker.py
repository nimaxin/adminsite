from dataclasses import dataclass
from string import Formatter
from typing import TYPE_CHECKING, Any

from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import PermissionDeniedError
from adminsite.fields import RelationField
from adminsite.query import CountMode, Page, QuerySpec
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

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

    admin: "Admin"
    item: RelationField
    request: Any = None

    @property
    def view(self) -> ModelView | None:
        """The view registered for the model this link points at."""
        return self.admin.views.for_model(self.item.target)

    @property
    def repository(self) -> SQLAlchemyRepository:
        """The target model's repository, for naming and keys."""
        return SQLAlchemyRepository(self.item.target, self.admin.inspector)

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
            named = view.get_search_fields(self.request)
            if named:
                return named
        return self._named_in_label()

    def _named_in_label(self) -> tuple[str, ...]:
        """The text columns the picker's own labels are built from."""
        view = self.view
        template = self.item.display_template or ""
        if view is not None and view.display_template:
            template = view.display_template
        if not template:
            return ()
        fields = self.admin.inspector.inspect(self.item.target).fields
        return tuple(
            name
            for _text, name, _spec, _conversion in Formatter().parse(template)
            if name and name in fields and fields[name].python_type is str
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
            return await self.repository.list(session, spec)
        return await view.fetch_page(session, spec, request=self.request)

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
            return await self.repository.get(session, key)
        try:
            return await view.fetch_record(session, key, request=self.request)
        except PermissionDeniedError:
            return None
