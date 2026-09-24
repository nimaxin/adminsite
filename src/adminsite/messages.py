from dataclasses import dataclass
from typing import Any

from markupsafe import Markup


@dataclass(frozen=True, slots=True)
class Message:
    """What an action answers, when a line of text that fades is not enough.

    ```python
    return Message("The new key is ready.", copy=key)
    return Message("The export is on its way.", link=url, link_text="Exports")
    return Message(Html("Queued <b>40</b> checks."), sticky=True)
    ```

    - `text` says what happened. It is escaped, unless given as `Html`.
    - `sticky` keeps it on screen until it is closed, where a message that
      all went well otherwise fades after a few seconds.
    - `link`, with `link_text`, is an address to follow, after the text.
    - `copy` is a value shown with a button that copies it, such as a new
      API key. It keeps the message on screen, and the audit log, which
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
