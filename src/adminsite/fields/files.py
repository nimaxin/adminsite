import mimetypes
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import UploadFile

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.files import FileStorage, name_of

MEGABYTE = 1024 * 1024

IMAGE_STARTS = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def looks_like_image(head: bytes) -> bool:
    """Whether a file starts the way a PNG, JPEG, GIF or WebP does.

    Checking the bytes, not the name, means a script renamed to .png is
    still refused.
    """
    if head.startswith(IMAGE_STARTS):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


class Unchanged:
    """The form left a file field alone, so the stored file stays."""

    def __repr__(self) -> str:
        return "UNCHANGED"


UNCHANGED = Unchanged()


@dataclass(frozen=True)
class NewFile:
    """An upload waiting to be stored when the record is saved."""

    upload: UploadFile


class FileField(Field):
    """An uploaded file, kept in a storage, its key kept in a string column.

    ```python
    FileField("invoice", storage=LocalStorage("uploads"), accept=".pdf")
    ```
    """

    widget = "file"

    def __init__(
        self,
        name: str,
        *,
        storage: FileStorage,
        accept: str = "",
        max_size: int = 10 * MEGABYTE,
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.storage = storage
        self.accept = accept
        self.max_size = max_size

    def display(self, value: Any) -> str:
        """The file's original name."""
        return name_of(value) if value else ""

    def content_type(self, key: str) -> str:
        """The type of a stored file, guessed from its name."""
        return mimetypes.guess_type(name_of(key))[0] or "application/octet-stream"

    def parse_upload(self, raw: Any, *, remove: bool, has_file: bool) -> Any:
        """Read a file input: a new file, None to remove, or UNCHANGED.

        `has_file` says whether the record already holds a file, since a
        required field is satisfied by the one already stored.
        """
        upload = raw if isinstance(raw, UploadFile) and raw.filename else None
        if upload is not None:
            self.check(upload)
            return NewFile(upload)
        if remove or not has_file:
            if self.required:
                raise FieldValidationError(self.name, "Choose a file.")
            return None if remove else UNCHANGED
        return UNCHANGED

    def check(self, upload: UploadFile) -> None:
        """Refuse a file that is too big or not of an accepted type."""
        if upload.size is not None and upload.size > self.max_size:
            limit = self.max_size / MEGABYTE
            raise FieldValidationError(self.name, f"Keep the file under {limit:g} MB.")
        if self.accept and not self.accepts(upload):
            raise FieldValidationError(
                self.name, f"Choose a file of this type: {self.accept}."
            )

    def accepts(self, upload: UploadFile) -> bool:
        """Whether the upload matches `accept`, as a browser reads it."""
        filename = (upload.filename or "").lower()
        content_type = (upload.content_type or "").lower()
        for wanted in (part.strip().lower() for part in self.accept.split(",")):
            if not wanted:
                continue
            if wanted.startswith(".") and filename.endswith(wanted):
                return True
            if wanted.endswith("/*") and content_type.startswith(wanted[:-1]):
                return True
            if wanted == content_type:
                return True
        return False


class ImageField(FileField):
    """An uploaded picture, shown as a thumbnail in the list and the form.

    Takes PNG, JPEG, GIF and WebP, checked by the file's first bytes, not
    just its name. SVG is left out on purpose: it can carry script.
    """

    widget = "image"

    def __init__(
        self,
        name: str,
        *,
        storage: FileStorage,
        accept: str = "image/png,image/jpeg,image/gif,image/webp",
        max_size: int = 5 * MEGABYTE,
        **options: Any,
    ) -> None:
        super().__init__(
            name, storage=storage, accept=accept, max_size=max_size, **options
        )

    def check(self, upload: UploadFile) -> None:
        """Refuse anything that is not really one of the image types."""
        super().check(upload)
        head = upload.file.read(12)
        upload.file.seek(0)
        if not looks_like_image(head):
            raise FieldValidationError(self.name, "This file is not a picture.")
