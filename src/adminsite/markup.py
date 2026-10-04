"""Text written into a page as markup, and how a value reads as text."""

from typing import Any

from markupsafe import Markup

__all__ = [
    "Html",
    "as_text",
    "plain",
]


class Html(Markup):
    """Text written into the page as markup instead of being escaped.

    A field that returns it can put a link, a badge or an icon in a cell:

    ```python
    def tracking(order: Order) -> Html:
        return Html('<a href="{}">Track</a>').format(order.tracking_url)


    ComputedField("tracking", tracking, needs=[Order.tracking_url])
    ```

    Everything interpolated with `format` or `%` is escaped, so a value from
    the database cannot carry markup of its own into the page. Anything
    written straight into the string is not, so keep that to markup you wrote.

    The CSV export and the JSON API send the text without the tags, since
    markup belongs on the page and not in a spreadsheet.
    """


def plain(text: str) -> str:
    """The text of a value, without any markup it carries."""
    return text.striptags() if isinstance(text, Markup) else text


def as_text(value: Any) -> str:
    """A value as text, leaving markup marked as markup."""
    return value if isinstance(value, Markup) else str(value)
