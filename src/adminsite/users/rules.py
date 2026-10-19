"""What a username and a password have to be."""

from adminsite.i18n import ngettext

__all__ = [
    "SHORTEST_PASSWORD",
    "normal_username",
    "password_problem",
]

# The fewest characters a password may have, as Django asks by default.
SHORTEST_PASSWORD = 8


def normal_username(username: str) -> str:
    """A username as it is kept: no spaces around it, in small letters."""
    return username.strip().lower()


def password_problem(password: str) -> str | None:
    """What is wrong with a new password, or None when it will do."""
    if len(password) < SHORTEST_PASSWORD:
        return ngettext(
            "Enter at least {count} character.",
            "Enter at least {count} characters.",
            SHORTEST_PASSWORD,
        )
    return None
