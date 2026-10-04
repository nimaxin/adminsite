from dataclasses import dataclass
from typing import Any

from markupsafe import Markup
from starlette.requests import Request

__all__ = [
    "Message",
    "add_message",
    "read_messages",
    "stored_message",
]


@dataclass(frozen=True, slots=True)
class Message:
    """What an action answers, when a line of text that fades is not enough.

    ```python
    Message("The new key is ready.", copy="sk_live_k3j9x2")
    Message("The export is on its way.", link="/admin/exports", link_text="Exports")
    Message(Html("Queued <b>40</b> checks."), sticky=True)
    ```

    Args:
        text: What happened. It is escaped, unless given as `Html`.
        sticky: Whether it stays on screen until it is closed, where a
            message that all went well otherwise fades after a few seconds.
        link: An address to follow, written after the text.
        link_text: The words of the link. Left empty, "Open".
        copy: A value shown with a button that copies it, such as a new API
            key. It keeps the message on screen, and the audit log, which
            keeps the text, never keeps it.
    """

    text: str
    sticky: bool = False
    link: str = ""
    link_text: str = ""
    copy: str = ""

    def __str__(self) -> str:
        return str(self.text)

    @property
    def stays(self) -> bool:
        """Whether it stays on screen until it is closed."""
        return self.sticky or bool(self.copy)

    def as_json(self) -> dict[str, Any]:
        """The message as the JSON API answers it."""
        found: dict[str, Any] = {"message": str(self.text)}
        if self.link:
            found["link"] = self.link
        if self.copy:
            found["copy"] = self.copy
        return found


def stored_message(answer: Any, kind: str) -> dict[str, Any]:
    """A message as the session keeps it until the next page shows it."""
    if isinstance(answer, Message):
        return {
            "text": str(answer.text),
            "html": isinstance(answer.text, Markup),
            "kind": kind,
            "sticky": answer.stays,
            "link": answer.link,
            "link_text": answer.link_text,
            # Not "copy": the page reads these as attributes, and a dict's
            # own copy method would be found first.
            "to_copy": answer.copy,
        }
    return {"text": str(answer), "html": isinstance(answer, Markup), "kind": kind}


def read_messages(request: Request) -> list[dict[str, str]]:
    """Take the one time messages left by the last request."""
    session = request.scope.get("session")
    if not session:
        return []
    messages = session.pop("adminsite_messages", [])
    return list(messages)


def add_message(request: Request, text: "str | Message", kind: str = "info") -> None:
    """Leave a message for the page the user lands on next."""
    session = request.scope.get("session")
    if session is None:
        return
    # Assigned, not appended in place: the session is only saved when one of
    # its keys is set, so a change inside the list would be lost.
    waiting = list(session.get("adminsite_messages", []))
    session["adminsite_messages"] = [*waiting, stored_message(text, kind)]
