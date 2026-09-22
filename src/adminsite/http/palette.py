from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

from adminsite.http.urls import Urls
from adminsite.i18n import gettext as _
from adminsite.query import CountMode
from adminsite.security import Permission

if TYPE_CHECKING:
    from adminsite.admin import Admin

# Records are only looked up once this much has been typed.
MIN_SEARCH = 2
PER_VIEW = 5


@dataclass
class PaletteItem:
    """One thing the palette can jump to."""

    label: str
    url: str
    hint: str = ""


@dataclass
class PaletteSection:
    """A heading in the palette and the items under it."""

    title: str
    items: list[PaletteItem] = field(default_factory=list)


async def palette(admin: "Admin", request: Request) -> Response:
    """What the command palette offers for what has been typed."""
    term = request.query_params.get("q", "").strip()
    sections = [await pages_matching(admin, request, term)]
    if len(term) >= MIN_SEARCH:
        sections += await records_matching(admin, request, term)
    shown = [section for section in sections if section.items]
    return await admin.render("_palette.html", request, {"sections": shown})


async def pages_matching(admin: "Admin", request: Request, term: str) -> PaletteSection:
    """The pages of the admin whose name contains the term."""
    urls = Urls(request)
    found = [PaletteItem(_("Overview"), urls.index())]
    for view in await admin.views_allowing(request):
        found.append(PaletteItem(view.label_plural, urls.list(view), view.group))
        if await view.allows(Permission.CREATE, request=request):
            found.append(
                PaletteItem(
                    _("New {thing}", thing=view.label.lower()), urls.create(view)
                )
            )
    for page in await admin.pages_allowing(request):
        found.append(PaletteItem(page.label, urls.page(page.name), page.group))
    if await admin.history_views(request):
        found.append(PaletteItem(_("Activity"), urls.activity()))

    wanted = term.lower()
    return PaletteSection(
        _("Pages"), [item for item in found if wanted in item.label.lower()]
    )


async def records_matching(
    admin: "Admin", request: Request, term: str
) -> list[PaletteSection]:
    """A few records from every searchable view, found by its own search."""
    urls = Urls(request)
    sections = []
    async with admin.database.session() as session:
        for view in await admin.views_allowing(request):
            if not view.global_search or not view.get_search_fields(request):
                continue
            # A view with no record page opens the form instead.
            opens_detail = await view.allows(Permission.DETAIL, request=request)
            if not opens_detail and not await view.allows(
                Permission.EDIT, request=request
            ):
                continue
            spec = view.build_spec(request=request, search=term).replace(
                limit=PER_VIEW, offset=0, count=CountMode.NONE, keyset=False
            )
            page = await view.fetch_page(session, spec, request=request)
            sections.append(
                PaletteSection(
                    view.label_plural,
                    [
                        PaletteItem(
                            view.title_of(record),
                            urls.detail(view, view.identity_of(record))
                            if opens_detail
                            else urls.edit(view, view.identity_of(record)),
                        )
                        for record in page
                    ],
                )
            )
    return sections
