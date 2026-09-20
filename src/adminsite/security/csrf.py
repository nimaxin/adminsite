import secrets

from markupsafe import Markup
from starlette.requests import Request

FIELD_NAME = "_csrf"
SESSION_KEY = "adminsite_csrf"
TOKEN_HEADER = "x-csrf-token"


def token_for(request: Request) -> str:
    """The token for this session, made on first use."""
    session = request.scope.get("session")
    if session is None:
        return ""
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return str(token)


def hidden_input(request: Request) -> Markup:
    """The hidden field every form in the admin carries."""
    token = token_for(request)
    if not token:
        return Markup("")
    return Markup(f'<input type="hidden" name="{FIELD_NAME}" value="{token}">')


def is_valid(request: Request, submitted: str | None) -> bool:
    """Whether a submitted token matches the one in the session."""
    session = request.scope.get("session")
    if session is None:
        # Without a session there is nothing to protect: no cookie is
        # carrying the user's identity.
        return True
    expected = session.get(SESSION_KEY)
    if not expected or not submitted:
        return False
    return secrets.compare_digest(str(expected), submitted)
