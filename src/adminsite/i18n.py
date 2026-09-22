"""Translations: English text is the key, each language a JSON file of it.

```python
from adminsite.i18n import gettext as _

message = _("{label} created.", label=view.label)
```

The language is set once per request, so code deep inside a view or a
field can translate without being handed the request.
"""

import json
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from functools import cache
from importlib import resources
from typing import Any

DEFAULT_LANGUAGE = "en"

# Languages written right to left, so the page is mirrored.
RIGHT_TO_LEFT = frozenset({"ar", "fa", "he", "ps", "ur", "ckb", "yi"})

# What each language calls itself, for the language menu.
NATIVE_NAMES = {
    "ar": "العربية",
    "de": "Deutsch",
    "en": "English",
    "es": "Español",
    "fa": "فارسی",
    "fr": "Français",
    "it": "Italiano",
    "nl": "Nederlands",
    "pt": "Português",
    "ru": "Русский",
    "tr": "Türkçe",
}

current_language: ContextVar[str] = ContextVar(
    "adminsite_language", default=DEFAULT_LANGUAGE
)
_extra: ContextVar[Mapping[str, Mapping[str, str]] | None] = ContextVar(
    "adminsite_translations", default=None
)


@cache
def built_in(language: str) -> dict[str, str]:
    """The catalog adminsite ships for a language, or nothing."""
    try:
        text = (
            resources.files("adminsite")
            .joinpath("locales", f"{language}.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError):
        return {}
    catalog: dict[str, str] = json.loads(text)
    return catalog


def shipped_languages() -> list[str]:
    """The languages adminsite has a catalog for, English included."""
    folder = resources.files("adminsite").joinpath("locales")
    found = {
        entry.name.removesuffix(".json")
        for entry in folder.iterdir()
        if entry.name.endswith(".json")
    }
    return sorted(found | {DEFAULT_LANGUAGE})


def gettext(text: str, **values: Any) -> str:
    """Translate a piece of English text into the current language.

    A project's own translations win over the built in ones, and text with
    no translation stays in English rather than disappearing.
    """
    language = current_language.get()
    translated = text
    if language != DEFAULT_LANGUAGE:
        own = (_extra.get() or {}).get(language, {})
        translated = own.get(text) or built_in(language).get(text) or text
    return translated.format(**values) if values else translated


def direction(language: str) -> str:
    """Which way a language is written: rtl or ltr."""
    return "rtl" if language.split("-")[0] in RIGHT_TO_LEFT else "ltr"


def native_name(language: str) -> str:
    """What a language calls itself."""
    return NATIVE_NAMES.get(language, language)


def negotiate(header: str, offered: Iterable[str]) -> str | None:
    """The best of the offered languages for an Accept-Language header."""
    available = {code.lower(): code for code in offered}
    wanted: list[tuple[float, str]] = []
    for part in header.split(","):
        tag, _, params = part.strip().partition(";")
        if not tag:
            continue
        weight = 1.0
        if params.strip().startswith("q="):
            try:
                weight = float(params.strip()[2:])
            except ValueError:
                continue
        wanted.append((weight, tag.strip().lower()))
    for _weight, tag in sorted(wanted, key=lambda item: -item[0]):
        for candidate in (tag, tag.split("-")[0]):
            if candidate in available:
                return available[candidate]
    return None


def activate(
    language: str, translations: Mapping[str, Mapping[str, str]] | None = None
) -> None:
    """Make a language the one this request speaks."""
    current_language.set(language)
    _extra.set(translations or {})
