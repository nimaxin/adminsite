import secrets
from urllib.parse import urlsplit

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
        # Without a session there is no token to check. There is still
        # something to protect: an admin left open on a private network is
        # reachable from any page the person's browser opens, so the
        # browser is asked where the request came from instead.
        return from_this_site(request)
    expected = session.get(SESSION_KEY)
    if not expected or not submitted:
        return False
    return secrets.compare_digest(str(expected), submitted)


def from_this_site(request: Request) -> bool:
    """Whether the browser says the request came from this site.

    Browsers send `Sec-Fetch-Site` and `Origin` with a form post and a
    page cannot change either. A request without them, from a script or
    an old browser, is let through, as it was before.
    """
    if request.headers.get("sec-fetch-site", "") == "cross-site":
        return False
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == request.url.netloc.lower()
