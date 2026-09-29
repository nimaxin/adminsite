from typing import TYPE_CHECKING

from adminsite.auth.passwords import hash_password, verify_password
from adminsite.auth.provider import AuthProvider, PasswordAuth
from adminsite.exceptions import SignInRefusedError, renamed_names

__all__ = [
    "AuthProvider",
    "PasswordAuth",
    "SignInRefusedError",
    "hash_password",
    "verify_password",
]


if not TYPE_CHECKING:
    # The names 0.1.0a10 changed, refused with the name each has now. Hidden
    # from type checkers, which report an old name as missing.
    __getattr__ = renamed_names(__name__, {"SignInRefused": "SignInRefusedError"})
