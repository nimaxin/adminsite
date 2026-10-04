"""Keys on the forms: which ones people type, and when they are fixed."""

import re
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.applications import Starlette

from adminsite import Admin, AdminSiteError, Field, Inline, ModelView, SaveContext
from adminsite._importing import build_plan
from adminsite.database import Database
from adminsite.fields import RelationField
from adminsite.inspector import SQLAlchemyInspector
from adminsite.views.picker import Picker
from tests.models import Shelf
from tests.support import Backend, request_from


class KeyBase(DeclarativeBase):
    """Kept apart from the test models, so only these tests make the tables."""


class Coupon(KeyBase):
    """A key the model makes up."""

    __tablename__ = "key_coupons"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20))


class Country(KeyBase):
    """A key people type."""

    __tablename__ = "key_countries"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))


class Member(KeyBase):
    __tablename__ = "key_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))

    visits: Mapped[list["Visit"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )
    # One way: a stop has no link back to its member.
    stops: Mapped[list["Stop"]] = relationship(cascade="all, delete-orphan")

    def __str__(self) -> str:
        return self.name


class Person(KeyBase):
    __tablename__ = "key_people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))

    # One to one, with the key on the passport's side.
    passport: Mapped["Passport | None"] = relationship(back_populates="person")


class Passport(KeyBase):
    __tablename__ = "key_passports"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("key_people.id"))

    person: Mapped[Person] = relationship(back_populates="passport")


class Profile(KeyBase):
    """One profile per member, keyed by the member's key."""

    __tablename__ = "key_profiles"

    member_id: Mapped[int] = mapped_column(
        ForeignKey("key_members.id"), primary_key=True
    )
    bio: Mapped[str] = mapped_column(String(60))

    member: Mapped[Member] = relationship()


class Visit(KeyBase):
    """Numbered within its member, so the member's key is part of its own."""

    __tablename__ = "key_visits"

    member_id: Mapped[int] = mapped_column(
        ForeignKey("key_members.id"), primary_key=True
    )
    number: Mapped[int] = mapped_column(primary_key=True)
    note: Mapped[str | None] = mapped_column(String(60), default=None)

    member: Mapped[Member] = relationship(back_populates="visits")


class Card(KeyBase):
    """Keyed by its member, through a column the database calls memberId."""

    __tablename__ = "key_cards"

    member_id: Mapped[int] = mapped_column(
        "memberId", ForeignKey("key_members.id"), primary_key=True
    )
    text: Mapped[str] = mapped_column(String(60))

    member: Mapped[Member] = relationship()


class Note(KeyBase):
    """Numbered within its member, through a column the database calls memberId."""

    __tablename__ = "key_notes"

    member_id: Mapped[int] = mapped_column(
        "memberId", ForeignKey("key_members.id"), primary_key=True
    )
    number: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(String(60))

    member: Mapped[Member] = relationship(back_populates="notes")


class Stop(KeyBase):
    """Numbered within its member, with no relationship back to it."""

    __tablename__ = "key_stops"

    member_id: Mapped[int] = mapped_column(
        ForeignKey("key_members.id"), primary_key=True
    )
    number: Mapped[int] = mapped_column(primary_key=True)
    place: Mapped[str] = mapped_column(String(60))


class Box(KeyBase):
    """Links to a shelf by the shelf's two key columns."""

    __tablename__ = "key_boxes"
    __table_args__ = (
        ForeignKeyConstraint(["shelf_aisle", "shelf_slot"], [Shelf.aisle, Shelf.slot]),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    shelf_aisle: Mapped[str | None] = mapped_column(String(2))
    shelf_slot: Mapped[int | None] = mapped_column()

    shelf: Mapped[Shelf | None] = relationship()


class CouponView(ModelView[Coupon]):
    pass


class CountryView(ModelView[Country]):
    pass


class ProfileView(ModelView[Profile]):
    pass


class NamedKeyProfileView(ModelView[Profile]):
    name = "named_profiles"
    fields = [Profile.member_id, Profile.bio]


class ShelfView(ModelView[Shelf]):
    name = "shelves"


class LockedShelfView(ModelView[Shelf]):
    name = "locked_shelves"
    fields = [Field(Shelf.aisle, read_only=True), Shelf.slot, Shelf.label]


class BoxView(ModelView[Box]):
    fields = [Box.name, Box.shelf]


class CardView(ModelView[Card]):
    fields = [Card.member, Card.text]


class MemberWithNotesView(ModelView[Member]):
    name = "members"
    fields = [Member.name]
    inlines = [Inline(Member.notes, fields=[Note.member_id, Note.number, Note.body])]


class MemberWithStopsView(ModelView[Member]):
    name = "members"
    fields = [Member.name]
    inlines = [Inline(Member.stops)]


class MemberWithNamedStopsView(MemberWithStopsView):
    inlines = [Inline(Member.stops, fields=[Stop.member_id, Stop.number, Stop.place])]


class TestALinkHeldByTheOtherModel:
    def test_the_model_keeps_its_own_key_among_its_fields(self) -> None:
        class PersonView(ModelView[Person]):
            pass

        class PassportView(ModelView[Passport]):
            pass

        assert PersonView()._settings.candidates == ("id", "name")
        assert PassportView()._settings.candidates == ("id", "person")

    def test_leaving_the_key_off_a_page_is_not_said_to_name_the_link(self) -> None:
        class PersonView(ModelView[Person]):
            fields = [Person.name, Person.passport]
            exclude_fields_from_list = [Person.id]

        with pytest.raises(AdminSiteError) as refused:
            PersonView()

        assert "shown as its relationship" not in str(refused.value)
        assert "is not among the fields the view shows" in str(refused.value)


class TestWhichKeysAreTyped:
    def test_the_inspector_says_which_keys_fill_themselves(self) -> None:
        inspector = SQLAlchemyInspector()

        assert inspector.inspect(Coupon).field_named("id").has_default
        assert inspector.inspect(Member).field_named("id").has_default
        assert not inspector.inspect(Country).field_named("code").has_default
        assert not inspector.inspect(Profile).field_named("member_id").has_default

    def test_the_inspector_names_a_link_s_columns_by_attribute(self) -> None:
        inspector = SQLAlchemyInspector()

        card = inspector.inspect(Card).relation_named("member")
        notes = inspector.inspect(Member).relation_named("notes")

        assert card.local_columns == ("member_id",)
        assert card.remote_columns == ("id",)
        assert notes.local_columns == ("id",)
        assert notes.remote_columns == ("member_id",)

    def test_a_key_the_model_fills_in_is_off_both_forms(self) -> None:
        view = CouponView()

        assert view._pages.form_fields(request_from()) == ("code",)
        assert view._pages.form_fields(
            request_from(), record=Coupon(code="SAVE10")
        ) == ("code",)

    def test_a_key_people_type_is_on_the_form_and_required(self) -> None:
        view = CountryView()

        assert view._pages.form_fields(request_from()) == ("code", "name")
        assert view._fields.field_for("code").required
        assert ShelfView()._fields.field_for("slot").required

    def test_it_is_fixed_once_the_record_exists(self) -> None:
        view = ShelfView()

        assert view._pages.readonly_paths(request_from()) == ()
        assert view._pages.readonly_paths(request_from(), Shelf(aisle="A", slot=1)) == (
            "aisle",
            "slot",
        )

    def test_so_is_a_link_made_of_key_columns(self) -> None:
        view = ProfileView()

        assert view._pages.form_fields(request_from()) == ("member", "bio")
        assert view._fields.field_for("member").required
        assert view._pages.readonly_paths(request_from()) == ()
        assert view._pages.readonly_paths(request_from(), Profile(member_id=1)) == (
            "member",
        )

    def test_a_link_to_many_is_not_fixed(self) -> None:
        class MemberView(ModelView[Member]):
            fields = [Member.name, Member.visits]

        assert MemberView()._pages.readonly_paths(request_from(), Member(id=1)) == ()

    def test_a_foreign_key_the_view_names_is_on_the_form(self) -> None:
        view = NamedKeyProfileView()

        assert view._pages.form_fields(request_from()) == ("member_id", "bio")
        assert view._pages.readonly_paths(request_from(), Profile(member_id=1)) == (
            "member_id",
        )

    def test_an_inline_leaves_out_the_key_its_parent_fills_in(self) -> None:
        class MemberView(ModelView[Member]):
            inlines = [
                Inline(
                    Member.visits, fields=[Visit.member_id, Visit.number, Visit.note]
                )
            ]

        child = MemberView()._inline_views["visits"]

        assert child._pages.form_fields(request_from()) == ("number", "note")

    def test_a_key_the_database_names_otherwise_is_shown_as_its_link(self) -> None:
        class PlainCardView(ModelView[Card]):
            pass

        assert PlainCardView()._pages.form_fields(request_from()) == ("member", "text")
        assert CardView()._pages.readonly_paths(request_from(), Card(member_id=1)) == (
            "member",
        )

    def test_an_inline_leaves_out_a_key_the_database_names_otherwise(self) -> None:
        class MemberView(ModelView[Member]):
            inlines = [Inline(Member.notes)]

        named = MemberWithNotesView()._inline_views["notes"]
        default = MemberView()._inline_views["notes"]

        assert named._pages.form_fields(request_from()) == ("number", "body")
        assert default._pages.form_fields(request_from()) == ("number", "body")
        assert default._pages.detail_fields(request_from()) == ("number", "body")

    @pytest.mark.parametrize("view", [MemberWithStopsView, MemberWithNamedStopsView])
    def test_an_inline_leaves_out_the_key_with_no_link_back(
        self, view: type[ModelView[Member]]
    ) -> None:
        child = view()._inline_views["stops"]

        assert child._pages.form_fields(request_from()) == ("number", "place")
        assert child._pages.readonly_paths(
            request_from(), Stop(member_id=1, number=1)
        ) == ("number",)
        assert "member_id" not in child._pages.detail_fields(request_from())


@pytest.fixture
async def database(backend: Backend) -> AsyncIterator[Database]:
    """The test database with these models' tables, a member and a country."""
    database = backend.database
    async with database.session() as session:
        await session.run(lambda plain: KeyBase.metadata.create_all(plain.connection()))
        await session.add(Member(name="Ana"))
        await session.add(Country(code="DE", name="Germany"))
        await session.commit()
    yield database
    async with database.session() as session:
        await session.run(lambda plain: KeyBase.metadata.drop_all(plain.connection()))
        await session.commit()


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[
            CouponView,
            CountryView,
            ProfileView,
            NamedKeyProfileView,
            ShelfView,
            LockedShelfView,
            ModelView[Member],
        ],
        api=True,
    )
    async with serve(admin) as client:
        yield client


class TestThePages:
    async def test_a_made_up_key_is_never_typed(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        new = await client.get("/admin/coupons/new")
        created = await client.post("/admin/coupons/new", data={"code": "SAVE10"})
        async with database.session() as session:
            coupon = await session.scalar(select(Coupon))
        assert coupon is not None
        edit = await client.get(f"/admin/coupons/{coupon.id}/edit")
        saved = await client.post(
            f"/admin/coupons/{coupon.id}/edit",
            data={"id": str(uuid.uuid4()), "code": "SAVE20"},
        )

        assert 'name="id"' not in new.text
        assert created.status_code == 303
        assert 'name="id"' not in edit.text
        assert saved.headers["location"] == f"/admin/coupons/{coupon.id}"
        async with database.session() as session:
            assert (await session.scalars(select(Coupon.id))).all() == [coupon.id]

    async def test_a_typed_key_left_blank_is_required(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.post("/admin/countries/new", data={"code": "", "name": "X"})
        api = await client.post("/admin/-/api/countries", json={"name": "X"})

        assert page.status_code == 422
        assert "This field is required." in page.text
        assert api.status_code == 422
        assert api.json()["errors"] == {"code": "This field is required."}
        async with database.session() as session:
            assert await session.scalar(select(func.count(Country.code))) == 1

    async def test_a_typed_key_is_read_only_once_saved(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        edit = await client.get("/admin/shelves/A,1/edit")
        saved = await client.post(
            "/admin/shelves/A,1/edit",
            data={"aisle": "Z", "slot": "9", "label": "Linen"},
        )
        landed = await client.get(saved.headers["location"])

        assert 'name="aisle"' not in edit.text
        assert 'id="field-aisle"' in edit.text
        assert saved.headers["location"] == "/admin/shelves/A,1"
        assert landed.status_code == 200
        async with database.session() as session:
            shelf = await session.get(Shelf, ("A", 1))
            assert shelf is not None
            assert shelf.label == "Linen"
            assert await session.get(Shelf, ("Z", 9)) is None

    async def test_a_key_marked_read_only_is_locked_on_both_forms(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        new = await client.get("/admin/locked_shelves/new")
        saved = await client.post(
            "/admin/locked_shelves/A,1/edit",
            data={"aisle": "Z", "slot": "9", "label": "Linen"},
        )

        assert 'name="aisle"' not in new.text
        assert 'name="slot"' in new.text
        assert saved.headers["location"] == "/admin/locked_shelves/A,1"
        async with database.session() as session:
            assert await session.get(Shelf, ("Z", 9)) is None

    async def test_the_api_refuses_to_change_a_key(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.patch("/admin/-/api/countries/DE", json={"code": "DD"})

        assert answer.status_code == 422
        assert answer.json()["errors"] == {"code": "This field cannot be written."}
        async with database.session() as session:
            assert await session.get(Country, "DE") is not None

    async def test_a_link_made_of_key_columns_is_set_once(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        new = await client.get("/admin/profiles/new")
        created = await client.post(
            "/admin/profiles/new", data={"member": "1", "bio": "Hi"}
        )
        edit = await client.get("/admin/profiles/1/edit")
        saved = await client.post(
            "/admin/profiles/1/edit", data={"member": "2", "bio": "Hello"}
        )

        assert 'name="member"' in new.text
        assert created.status_code == 303
        assert 'name="member"' not in edit.text
        assert saved.headers["location"] == "/admin/profiles/1"
        async with database.session() as session:
            profile = await session.get(Profile, 1)
            assert profile is not None
            assert profile.bio == "Hello"

    async def test_a_foreign_key_named_in_fields_creates_the_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        created = await client.post(
            "/admin/named_profiles/new", data={"member_id": "1", "bio": "Hi"}
        )

        assert created.status_code == 303, created.text
        async with database.session() as session:
            assert await session.get(Profile, 1) is not None

    async def test_a_link_over_a_key_the_database_names_otherwise_is_set_once(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            await session.add(Member(name="Bo"))
            await session.add(Card(member_id=1, text="Hi"))
            await session.commit()

        async with serve(Admin(database, views=[CardView], api=True)) as client:
            edit = await client.get("/admin/cards/1/edit")
            saved = await client.post(
                "/admin/cards/1/edit", data={"member": "2", "text": "Hello"}
            )
            patched = await client.patch("/admin/-/api/cards/1", json={"member": "2"})

        assert 'name="member"' not in edit.text
        assert saved.headers["location"] == "/admin/cards/1"
        assert patched.status_code == 422
        assert patched.json()["errors"] == {"member": "This field cannot be written."}
        async with database.session() as session:
            card = await session.get(Card, 1)
            assert card is not None
            assert card.text == "Hello"
            assert await session.get(Card, 2) is None

    @pytest.mark.parametrize(
        ("view", "model", "column"),
        [
            (MemberWithNotesView, Note, "body"),
            (MemberWithStopsView, Stop, "place"),
            (MemberWithNamedStopsView, Stop, "place"),
        ],
    )
    async def test_a_new_inline_row_takes_the_key_its_parent_fills_in(
        self,
        database: Database,
        view: type[ModelView[Member]],
        model: type[Note | Stop],
        column: str,
    ) -> None:
        inline = view.inlines[0].name

        async with serve(Admin(database, views=[view])) as client:
            edit = await client.get("/admin/members/1/edit")
            saved = await client.post(
                "/admin/members/1/edit",
                data={
                    "name": "Ana",
                    f"{inline}-count": "1",
                    f"{inline}-0-key": "",
                    f"{inline}-0-number": "1",
                    f"{inline}-0-{column}": "First",
                },
            )

        assert f'name="{inline}-0-number"' in edit.text
        assert f'name="{inline}-0-member_id"' not in edit.text
        assert saved.status_code == 303, saved.text
        async with database.session() as session:
            rows = (await session.scalars(select(model))).all()
        assert [(row.member_id, row.number) for row in rows] == [(1, 1)]


class Renaming(ModelView[Country]):
    """Moves a country to another code when it is saved."""

    name = "renamed"

    async def before_save(self, context: SaveContext[Country]) -> None:
        if not context.created:
            context.values[Country.code].set("AT")


class ImportedCountries(ModelView[Country]):
    name = "imported_countries"
    can_import = True


class TestAnImport:
    async def test_a_key_people_type_adds_the_record_it_names(
        self, database: Database
    ) -> None:
        table = [
            ["code", "name"],
            ["DE", "Deutschland"],
            ["FR", "France"],
            ["", "Nowhere"],
            ["FR", "Francia"],
        ]
        async with database.session() as session:
            plan = await build_plan(
                ImportedCountries(), session, table, request=request_from()
            )

        germany, france, nowhere, again = plan.rows
        assert (germany.action, germany.values) == ("update", {"name": "Deutschland"})
        assert (france.action, france.values) == (
            "create",
            {"code": "FR", "name": "France"},
        )
        assert nowhere.errors == {"code": "This field is required."}
        assert again.errors == {"code": "Row 3 already adds a country with the key FR."}

    async def test_a_file_without_it_adds_nothing(self, database: Database) -> None:
        async with database.session() as session:
            plan = await build_plan(
                ImportedCountries(),
                session,
                [["name"], ["France"]],
                request=request_from(),
            )

        assert plan.rows[0].errors == {"code": "Missing, and a new record needs it."}

    async def test_only_what_the_preview_passed_is_saved(
        self, database: Database
    ) -> None:
        async with serve(Admin(database, views=[ImportedCountries])) as client:
            page = await client.post(
                "/admin/imported_countries/import",
                files={"file": ("c.csv", b"code,name\nFR,France\n,Nowhere\nFR,X\n")},
            )
            action = re.search(r'action="([^"]+/import/[^"]+)"', page.text)
            assert action is not None
            done = await client.post(action.group(1))

        assert "1 new" in page.text
        assert "2 with problems" in page.text
        assert done.status_code == 303
        async with database.session() as session:
            names = (await session.scalars(select(Country.name))).all()
            assert sorted(names) == ["France", "Germany"]


class TestAKeyAHookChanges:
    async def test_the_edit_page_follows_the_record(self, database: Database) -> None:
        async with serve(Admin(database, views=[Renaming])) as client:
            saved = await client.post(
                "/admin/renamed/DE/edit", data={"name": "Austria"}
            )
            landed = await client.get(saved.headers["location"])

        assert saved.headers["location"] == "/admin/renamed/AT"
        assert landed.status_code == 200

    async def test_so_does_the_api(self, database: Database) -> None:
        async with serve(Admin(database, views=[Renaming], api=True)) as client:
            answer = await client.patch(
                "/admin/-/api/renamed/DE", json={"name": "Austria"}
            )

        assert answer.status_code == 200, answer.text
        assert answer.json()["key"] == "AT"


class TestAKeyOfTwoColumnsWrittenAsText:
    async def test_a_link_to_a_model_with_no_view_saves(
        self, database: Database
    ) -> None:
        async with serve(Admin(database, views=[BoxView])) as client:
            new = await client.get("/admin/boxes/new")
            created = await client.post(
                "/admin/boxes/new", data={"name": "Tees", "shelf": "A,1"}
            )

        assert 'value="A,1"' in new.text
        assert created.status_code == 303, created.text
        async with database.session() as session:
            box = await session.scalar(select(Box))
            assert box is not None
            assert (box.shelf_aisle, box.shelf_slot) == ("A", 1)

    @pytest.mark.parametrize("views", [[BoxView], [BoxView, ShelfView]])
    async def test_a_picker_finds_the_record_it_holds(
        self, database: Database, views: list[type[ModelView[Box] | ModelView[Shelf]]]
    ) -> None:
        admin = Admin(database, views=views)
        item = BoxView()._fields.field_for("shelf")
        assert isinstance(item, RelationField)
        picker = Picker(
            views=admin.views,
            inspector=SQLAlchemyInspector(),
            item=item,
            request=request_from(),
        )

        async with database.session() as session:
            found = await picker.get(session, "A,1")

        assert str(found) == "A1"

    async def test_the_api_answers_with_a_record_it_creates(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/shelves", json={"aisle": "Q", "slot": 7, "label": "Hats"}
        )

        assert answer.status_code == 201, answer.text
        assert answer.json()["key"] == "Q,7"
