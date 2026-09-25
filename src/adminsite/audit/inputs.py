import re
from collections.abc import Mapping, Sequence
from typing import Any

from starlette.datastructures import UploadFile

from adminsite.audit.entry import as_json

# A name holding one of these words, such as "password_hash", holds a
# secret. A field says otherwise with `secret=`.
SECRET_WORDS = frozenset(
    {"password", "passwd", "passphrase", "secret", "token", "pin", "otp"}
)

# "key" is a secret only after one of these, as in api_key or private_key:
# a sort key or a settings table's key is not.
SECRET_KEYS = frozenset(
    {"api", "private", "secret", "access", "signing", "encryption", "license"}
)

# What the log keeps in place of a secret.
HIDDEN = "***"


def looks_secret(name: str) -> bool:
    """Whether a name, such as "api_key" or "newPassword", names a secret."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    words = [word for word in re.split(r"[^a-z0-9]+", spaced) if word]
    if any(word in SECRET_WORDS for word in words):
        return True
    return any(
        word == "key" and index > 0 and words[index - 1] in SECRET_KEYS
        for index, word in enumerate(words)
    )


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
