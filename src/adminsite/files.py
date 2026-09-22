import re
import secrets
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import anyio
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response

CHUNK_SIZE = 1024 * 1024

# Types a browser shows inline. Anything else is sent as a download, so an
# uploaded HTML or SVG file can never run script on the admin's origin.
SHOWN_INLINE = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
)


def safe_name(filename: str) -> str:
    """A file name with nothing in it that could climb out of a folder."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:80] or "file"
    suffix = re.sub(r"[^A-Za-z0-9]+", "", suffix)[:10].lower()
    return f"{stem}.{suffix}" if suffix else stem


def name_of(key: str) -> str:
    """The original name of a stored file, as its key keeps it."""
    base = PurePosixPath(key).name
    return base.split("-", 1)[1] if "-" in base else base


class FileStorage:
    """Where uploaded files go.

    Subclass it to keep files somewhere else, such as S3: `save` returns the
    key kept in the column, and `response` answers a request for the file,
    for example with a redirect to a signed URL.
    """

    async def save(self, upload: UploadFile) -> str:
        """Store an upload and return the key to keep in the column."""
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        """Remove a stored file. A missing file is not an error."""
        raise NotImplementedError

    def url(self, key: str) -> str:
        """A public address for the file, or nothing to serve it through the admin."""
        return ""

    async def response(self, key: str, content_type: str = "") -> Response:
        """Answer a request for the file, made through the admin."""
        raise HTTPException(status_code=404, detail="This file is not served here.")


class LocalStorage(FileStorage):
    """Keeps files in a folder on this machine.

    ```python
    uploads = LocalStorage("uploads")
    ```

    Files are served through the admin, behind its sign in, unless you give
    `url_prefix` for a place your application already serves them from.
    Each file gets a key such as `2026/09/k3j9x2-invoice.pdf`: the month,
    a random part so names never clash, and the original name.
    """

    def __init__(self, directory: str | Path, *, url_prefix: str = "") -> None:
        self.directory = Path(directory).resolve()
        self.url_prefix = url_prefix.rstrip("/")

    async def save(self, upload: UploadFile) -> str:
        """Write the upload into the folder, a chunk at a time."""
        month = datetime.now(UTC).strftime("%Y/%m")
        key = f"{month}/{secrets.token_urlsafe(6)}-{safe_name(upload.filename or '')}"
        target = self.path_for(key)
        await anyio.to_thread.run_sync(
            lambda: target.parent.mkdir(parents=True, exist_ok=True)
        )
        await upload.seek(0)
        async with await anyio.open_file(target, "wb") as written:
            while chunk := await upload.read(CHUNK_SIZE):
                await written.write(chunk)
        return key

    async def delete(self, key: str) -> None:
        """Remove the file, if it is still there."""
        target = self.path_for(key)
        await anyio.to_thread.run_sync(lambda: target.unlink(missing_ok=True))

    def url(self, key: str) -> str:
        """The public address, when the files are served elsewhere."""
        return f"{self.url_prefix}/{key}" if self.url_prefix else ""

    async def response(self, key: str, content_type: str = "") -> Response:
        """Send the file, as a download unless it is safe to show."""
        target = self.path_for(key)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="No such file.")
        inline = content_type in SHOWN_INLINE
        return FileResponse(
            target,
            media_type=content_type or None,
            filename=name_of(key),
            content_disposition_type="inline" if inline else "attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    def path_for(self, key: str) -> Path:
        """Where a key lives on disk, refusing any key that leaves the folder."""
        target = (self.directory / key).resolve()
        if not target.is_relative_to(self.directory) or target == self.directory:
            raise HTTPException(status_code=404, detail="No such file.")
        return target
