import re
from collections.abc import Mapping, Sequence
from typing import Any

from starlette.datastructures import UploadFile

from adminsite.audit.entry import as_json

# A name made of one of these words, such as "password" or "api_key", holds
# a secret. A field says otherwise with `secret=`.
SECRET_WORDS = frozenset(
    {"password", "passwd", "passphrase", "secret", "token", "key", "pin", "otp"}
)

# What the log keeps in place of a secret.
HIDDEN = "***"


def looks_secret(name: str) -> bool:
    """Whether a name, such as "api_key" or "newPassword", names a secret."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return any(word in SECRET_WORDS for word in re.split(r"[^a-z0-9]+", spaced))


def recorded_inputs(fields: Sequence[Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The values an action was run with, as the audit log keeps them.

    A secret is kept as "***", and a file by its name, type and size,
    never by what is in it.
    """
    kept: dict[str, Any] = {}
    for field in fields:
        if field.name not in values:
            continue
        secret = getattr(field, "secret", None)
        if secret is None:
            secret = looks_secret(field.name)
        kept[field.name] = HIDDEN if secret else recorded_value(values[field.name])
    return kept


def recorded_value(value: Any) -> Any:
    """One value as JSON can hold it, and a file as what it is, not its content."""
    upload = getattr(value, "upload", value)
    if isinstance(upload, UploadFile):
        return {
            "file": upload.filename,
            "type": upload.content_type,
            "size": upload.size,
        }
    return as_json(value)
