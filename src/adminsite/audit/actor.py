from typing import Any, TypedDict

from starlette.datastructures import Headers

# The key the admin puts the signed-in user's identity under in the scope,
# beside the user itself under "user_record".
USER_KEY = "user_key"

# Enough for any browser's description of itself, and no more.
AGENT_LIMIT = 300

# As long as the audit table's user column.
NAME_LIMIT = 200


class Actor(TypedDict):
    """Who made a request and from where, as an audit entry holds it."""

    user: str | None
    user_key: str | None
    ip: str | None
    user_agent: str | None


def actor_of(request: Any) -> Actor:
    """Who made this request, and from where.

    The address is the one the ASGI server worked out. Behind a proxy it is
    the client's own only when the server is told to trust that proxy, for
    example with uvicorn's `--forwarded-allow-ips`.
    """
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return Actor(user=None, user_key=None, ip=None, user_agent=None)
    user = scope.get("user_record")
    key = scope.get(USER_KEY)
    client = scope.get("client")
    agent = Headers(raw=scope.get("headers") or []).get("user-agent")
    return Actor(
        user=str(user)[:NAME_LIMIT] if user is not None else None,
        user_key=str(key) if key is not None else None,
        ip=str(client[0]) if client else None,
        user_agent=agent[:AGENT_LIMIT] if agent else None,
    )
