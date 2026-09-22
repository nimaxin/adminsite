import io
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.datastructures import Headers, UploadFile
from starlette.exceptions import HTTPException

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import FieldValidationError, RefusedError
from adminsite.fields import FileField, ImageField
from adminsite.fields.files import UNCHANGED, NewFile
from adminsite.files import LocalStorage, name_of, safe_name
from adminsite.views.writing import SaveContext
from tests.models import Product

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def upload(name: str, data: bytes, content_type: str = "") -> UploadFile:
    headers = Headers({"content-type": content_type}) if content_type else None
    return UploadFile(io.BytesIO(data), size=len(data), filename=name, headers=headers)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


class TestNames:
    def test_a_name_cannot_climb_out(self) -> None:
        assert safe_name("../../etc/passwd") == "passwd"
        assert safe_name("C:\\Users\\me\\My Invoice (1).PDF") == "My-Invoice-1.pdf"
        assert safe_name("") == "file"

    def test_the_key_keeps_the_original_name(self) -> None:
        assert name_of("2026/09/abc123-invoice.pdf") == "invoice.pdf"


class TestLocalStorage:
    async def test_a_saved_file_can_be_read_back(self, storage: LocalStorage) -> None:
        key = await storage.save(upload("invoice.pdf", b"%PDF-1.7"))

        assert key.endswith("-invoice.pdf")
        assert storage.path_for(key).read_bytes() == b"%PDF-1.7"

    async def test_deleting_removes_the_file(self, storage: LocalStorage) -> None:
        key = await storage.save(upload("a.txt", b"hello"))

        await storage.delete(key)
        await storage.delete(key)

        assert not storage.path_for(key).exists()

    def test_a_key_that_leaves_the_folder_is_refused(
        self, storage: LocalStorage
    ) -> None:
        with pytest.raises(HTTPException):
            storage.path_for("../outside.txt")

    async def test_only_safe_types_are_shown_inline(
        self, storage: LocalStorage
    ) -> None:
        picture = await storage.save(upload("cat.png", PNG))
        page = await storage.save(upload("page.html", b"<script>"))

        shown = await storage.response(picture, "image/png")
        downloaded = await storage.response(page, "text/html")

        assert shown.headers["content-disposition"].startswith("inline")
        assert downloaded.headers["content-disposition"].startswith("attachment")
        assert downloaded.headers["x-content-type-options"] == "nosniff"

    def test_files_elsewhere_have_a_public_address(self, tmp_path: Path) -> None:
        public = LocalStorage(tmp_path, url_prefix="https://cdn.example.com/media/")

        assert (
            public.url("2026/09/a-b.png")
            == "https://cdn.example.com/media/2026/09/a-b.png"
        )


class TestFileField:
    def test_a_new_upload_is_taken(self, storage: LocalStorage) -> None:
        field = FileField("invoice", storage=storage)

        found = field.parse_upload(upload("a.pdf", b"x"), remove=False, has_file=False)

        assert isinstance(found, NewFile)

    def test_no_upload_leaves_the_file(self, storage: LocalStorage) -> None:
        field = FileField("invoice", storage=storage)
        empty = upload("", b"")

        assert field.parse_upload(empty, remove=False, has_file=True) is UNCHANGED
        assert field.parse_upload(empty, remove=True, has_file=True) is None

    def test_a_required_file_needs_one(self, storage: LocalStorage) -> None:
        field = FileField("invoice", storage=storage, required=True)

        with pytest.raises(FieldValidationError, match="Choose a file"):
            field.parse_upload(None, remove=False, has_file=False)
        assert field.parse_upload(None, remove=False, has_file=True) is UNCHANGED

    def test_too_big_is_refused(self, storage: LocalStorage) -> None:
        field = FileField("invoice", storage=storage, max_size=4)

        with pytest.raises(FieldValidationError, match="under"):
            field.parse_upload(upload("a.pdf", b"12345"), remove=False, has_file=False)

    @pytest.mark.parametrize(
        ("accept", "name", "content_type", "taken"),
        [
            (".pdf", "a.PDF", "", True),
            (".pdf", "a.doc", "", False),
            ("image/*", "a.bin", "image/png", True),
            ("application/pdf", "a", "application/pdf", True),
            (".pdf,.csv", "a.csv", "", True),
        ],
    )
    def test_accept_works_as_in_the_browser(
        self,
        storage: LocalStorage,
        accept: str,
        name: str,
        content_type: str,
        taken: bool,
    ) -> None:
        field = FileField("invoice", storage=storage, accept=accept)

        assert field.accepts(upload(name, b"x", content_type)) is taken

    def test_a_picture_is_checked_by_its_bytes(self, storage: LocalStorage) -> None:
        field = ImageField("photo", storage=storage)
        real = upload("cat.png", PNG, "image/png")
        fake = upload("cat.png", b"<svg onload=alert(1)>", "image/png")

        assert isinstance(
            field.parse_upload(real, remove=False, has_file=False), NewFile
        )
        with pytest.raises(FieldValidationError, match="not a picture"):
            field.parse_upload(fake, remove=False, has_file=False)

    def test_svg_is_not_a_picture_by_default(self, storage: LocalStorage) -> None:
        field = ImageField("photo", storage=storage)

        assert not field.accepts(upload("a.svg", b"<svg/>", "image/svg+xml"))


def product_view(storage: LocalStorage) -> type[ModelView]:
    class ProductView(ModelView, model=Product):
        form_fields = ("name", "price", "description")
        list_display = ("name", "description")
        fields = (ImageField("description", label="Photo", storage=storage),)

    return ProductView


@pytest.fixture
async def client(
    database: Database, storage: LocalStorage
) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, title="Shop", views=[product_view(storage)])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def photo_of(database: Database, key: int = 1) -> str | None:
    async with database.session() as session:
        product = await session.get(Product, key)
        assert product is not None
        return product.description


def files_in(storage: LocalStorage) -> list[Path]:
    return [path for path in storage.directory.rglob("*") if path.is_file()]


class TestUploading:
    async def test_the_form_can_send_files(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/products/1/edit")

        assert 'enctype="multipart/form-data"' in page.text
        assert 'type="file"' in page.text

    async def test_an_upload_is_stored_and_served(
        self, client: httpx.AsyncClient, database: Database, storage: LocalStorage
    ) -> None:
        answer = await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt", "price": "59.00"},
            files={"description": ("shirt.png", PNG, "image/png")},
        )
        key = await photo_of(database)
        assert key is not None
        served = await client.get(f"/admin/-/files/products/description/{key}")
        listed = await client.get("/admin/products")

        assert answer.status_code == 303
        assert key.endswith("-shirt.png")
        assert served.content == PNG
        assert served.headers["content-type"] == "image/png"
        assert f'src="/admin/-/files/products/description/{key}"' in listed.text

    async def test_saving_without_a_file_keeps_the_stored_one(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt", "price": "59.00"},
            files={"description": ("shirt.png", PNG, "image/png")},
        )
        before = await photo_of(database)
        await client.post(
            "/admin/products/1/edit", data={"name": "Linen top", "price": "59.00"}
        )

        assert await photo_of(database) == before

    async def test_a_new_file_replaces_and_deletes_the_old(
        self, client: httpx.AsyncClient, database: Database, storage: LocalStorage
    ) -> None:
        for name in ("one.png", "two.png"):
            await client.post(
                "/admin/products/1/edit",
                data={"name": "Linen shirt", "price": "59.00"},
                files={"description": (name, PNG, "image/png")},
            )

        key = await photo_of(database)
        assert key is not None
        assert key.endswith("-two.png")
        assert [path.name for path in files_in(storage)] == [Path(key).name]

    async def test_remove_clears_the_field_and_the_file(
        self, client: httpx.AsyncClient, database: Database, storage: LocalStorage
    ) -> None:
        await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt", "price": "59.00"},
            files={"description": ("one.png", PNG, "image/png")},
        )
        await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt", "price": "59.00", "description-remove": "on"},
        )

        assert await photo_of(database) is None
        assert files_in(storage) == []

    async def test_a_refused_file_says_why(
        self, client: httpx.AsyncClient, storage: LocalStorage
    ) -> None:
        answer = await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt", "price": "59.00"},
            files={"description": ("evil.png", b"<script>", "image/png")},
        )

        assert answer.status_code == 422
        assert "This file is not a picture." in answer.text
        assert files_in(storage) == []

    async def test_a_key_that_climbs_out_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.get("/admin/-/files/products/description/../../secret")

        assert answer.status_code == 404

    async def test_only_file_fields_serve_files(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.get("/admin/-/files/products/name/anything.png")

        assert answer.status_code == 404


class TestAFailedSave:
    async def test_the_new_file_is_thrown_away(
        self, database: Database, storage: LocalStorage
    ) -> None:
        class Refusing(product_view(storage)):  # type: ignore[misc]
            async def before_save(self, context: SaveContext) -> None:
                raise RefusedError("Not today.")

        view = Refusing()
        async with database.session() as session:
            record = await session.get(Product, 1)
            with pytest.raises(RefusedError):
                await view.save(
                    session,
                    {"description": NewFile(upload("a.png", PNG, "image/png"))},
                    record=record,
                )

        assert files_in(storage) == []
