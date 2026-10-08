"""Translations: English text is the key, each language a JSON file of it.

```python
from adminsite.i18n import gettext as _, ngettext

message = _("{thing} saved.", thing="Order")
shipped = ngettext("{count} order shipped.", "{count} orders shipped.", 12)
```

The language is set once per request, so code deep inside a view or a
field can translate without being handed the request.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from functools import cache
from importlib import resources
from typing import Any, NamedTuple

from markupsafe import Markup, escape

__all__ = [
    "DEFAULT_LANGUAGE",
    "NATIVE_NAMES",
    "RIGHT_TO_LEFT",
    "Translations",
    "activate",
    "built_in",
    "canonical",
    "current_language",
    "direction",
    "gettext",
    "in_sentence",
    "lower",
    "native_name",
    "negotiate",
    "ngettext",
    "plural_categories",
    "plural_category",
    "plural_forms",
    "shipped_languages",
    "upper",
]

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
    "ja": "日本語",
    "nl": "Nederlands",
    "pt": "Português",
    "pt-BR": "Português (Brasil)",
    "ru": "Русский",
    "tr": "Türkçe",
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
}

# Where Chinese is written in Traditional characters. Everywhere else, and
# Chinese asked for with no region, it is Simplified.
TRADITIONAL_REGIONS = frozenset({"TW", "HK", "MO"})

# Languages that write every noun with a capital, which a label keeps
# inside a sentence.
CAPITAL_NOUNS = frozenset({"de", "lb"})

# Languages with a dotted and a dotless i, whose capitals are İ and I.
DOTTED_I = frozenset({"tr", "az"})

# A translation is one text, or one text for each plural category it needs.
Translation = str | Mapping[str, str]
Translations = Mapping[str, Mapping[str, Translation]]

current_language: ContextVar[str] = ContextVar(
    "adminsite_language", default=DEFAULT_LANGUAGE
)
_extra: ContextVar[Translations | None] = ContextVar(
    "adminsite_translations", default=None
)


@cache
def built_in(language: str) -> dict[str, Translation]:
    """The catalog adminsite ships for a language, or nothing."""
    try:
        text = (
            resources.files("adminsite")
            .joinpath("locales", f"{language}.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError):
        return {}
    catalog: dict[str, Translation] = json.loads(text)
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


def _translation(text: str) -> Translation | None:
    """What the current language has for a text: the project's, else adminsite's."""
    language = current_language.get()
    own = (_extra.get() or {}).get(language, {})
    return own.get(text) or built_in(language).get(text)


def gettext(text: str, **values: Any) -> str:
    """Translate a piece of English text into the current language.

    A project's own translations win over the built in ones, English
    included, so a word of the admin's own can be changed without copying
    the template it is in. Text with no translation stays in English rather
    than disappearing.

    Args:
        text: The English text, with any values as named placeholders, such
            as "{thing} saved.".
        **values: The values for the placeholders, filled in after the text
            is translated.

    Returns:
        The text in the language of the current request.
    """
    found = _translation(text)
    if isinstance(found, Mapping):
        found = found.get("other")
    translated = found or text
    return _filled(translated, values) if values else translated


def ngettext(singular: str, plural: str, count: int, **values: Any) -> str:
    """Translate a text with a count into the form its language writes for it.

    English has two forms, "1 row" and "2 rows". Russian has three, and
    Arabic six, so a translation holds one text for each plural category its
    language uses, such as `{"one": ..., "few": ..., "many": ...}`, keyed by
    the English singular.

    Args:
        singular: The English text for one, such as "{count} row".
        plural: The English text for any other number, such as
            "{count} rows".
        count: The number that picks the form. It also fills `{count}`,
            with its thousands apart: 1,234.
        **values: The other placeholders.

    Returns:
        The text in the language of the current request.
    """
    found = _translation(singular)
    english = singular if count == 1 else plural
    if isinstance(found, Mapping):
        category = plural_category(current_language.get(), count)
        found = found.get(category) or found.get("other")
    return _filled(found or english, {"count": f"{count:,}", **values})


def _filled(text: str, values: Mapping[str, Any]) -> str:
    """Put the values in a text's placeholders.

    A value marked as markup, such as a link a template wrote, goes in as it
    is, and everything else in the text is escaped around it.
    """
    if any(isinstance(value, Markup) for value in values.values()):
        return escape(text).format(**values)
    return text.format(**values)


def plural_forms(singular: str, plural: str) -> dict[str, str]:
    """A text with a count in each of its forms, for the browser to pick from.

    The browser picks with `Intl.PluralRules`, which files numbers under the
    same categories, so a count that changes on the page reads right too.
    """
    found = _translation(singular)
    if isinstance(found, Mapping):
        forms = dict(found)
    elif found:
        forms = {"other": found}
    else:
        forms = {"one": singular, "other": plural}
    # A number the browser files under a category this language has no text
    # for, such as a million in French, reads as the last form does.
    forms.setdefault("other", list(forms.values())[-1])
    return forms


def _language(tag: str) -> str:
    """The language a tag names, without its script or region: pt-BR is pt."""
    return tag.split("-")[0].lower()


def _english(number: int) -> str:
    return "one" if number == 1 else "other"


def _french(number: int) -> str:
    return "one" if number in (0, 1) else "other"


def _russian(number: int) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return "one"
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return "few"
    return "many"


def _arabic(number: int) -> str:
    if number in (0, 1, 2):
        return ("zero", "one", "two")[number]
    if 3 <= number % 100 <= 10:
        return "few"
    if 11 <= number % 100 <= 99:
        return "many"
    return "other"


def _one_form(number: int) -> str:
    return "other"


# How each language counts, by the Unicode CLDR rules for whole numbers.
# Any other language counts as English does.
PLURAL_RULES: dict[str, Callable[[int], str]] = {
    "ar": _arabic,
    "fa": _french,
    "fr": _french,
    "ja": _one_form,
    "pt": _french,
    "ru": _russian,
    "uk": _russian,
    "zh": _one_form,
}


def plural_category(language: str, count: int) -> str:
    """The plural category a language files a number under, such as few."""
    rule = PLURAL_RULES.get(_language(language), _english)
    return rule(abs(count))


# The plural categories, in the order CLDR lists them.
CATEGORIES = ("zero", "one", "two", "few", "many", "other")


def plural_categories(language: str) -> list[str]:
    """The plural categories a language files whole numbers under: one, other."""
    used = {plural_category(language, number) for number in range(200)}
    return [category for category in CATEGORIES if category in used]


class _Tag(NamedTuple):
    """A language tag taken apart: zh-Hant-TW is zh, Hant and TW."""

    language: str
    script: str
    region: str


def _parts(tag: str) -> _Tag:
    language, *rest = canonical(tag).split("-")
    script = next((part for part in rest if len(part) == 4 and part.isalpha()), "")
    region = next((part for part in rest if _is_region(part)), "")
    if language == "zh" and not script:
        script = "Hant" if region in TRADITIONAL_REGIONS else "Hans"
    return _Tag(language, script, region)


def _is_region(part: str) -> bool:
    """Whether a part of a tag names a region: TW, or 419 for Latin America."""
    return (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit())


def canonical(tag: str) -> str:
    """A language tag written the usual way: pt-BR, zh-Hant, sr-Latn-RS."""
    first, *rest = tag.strip().replace("_", "-").split("-")
    parts = [first.lower()]
    for part in rest:
        if len(part) == 4 and part.isalpha():
            parts.append(part.title())
        elif _is_region(part):
            parts.append(part.upper())
        else:
            parts.append(part.lower())
    return "-".join(parts)


def _match(wanted: str, offered: list[str]) -> str | None:
    """The offered language closest to one a browser asks for, if any is close.

    Close means the same language, in the same script: zh-TW never matches
    Simplified Chinese. Among those the one asked for itself comes first,
    then one for the same region, then the first offered.
    """
    asked = _parts(wanted)
    best, closest = None, 0
    for code in offered:
        have = _parts(code)
        if have.language != asked.language:
            continue
        if have.script != asked.script and (have.script and asked.script):
            continue
        if have == asked:
            closeness = 3
        elif have.region == asked.region:
            closeness = 2
        else:
            closeness = 1
        if closeness > closest:
            best, closest = code, closeness
    return best


def negotiate(header: str, offered: Iterable[str]) -> str | None:
    """The best of the offered languages for an Accept-Language header.

    A browser that asks for a language in a region, such as fr-CA, gets
    that language, and one that asks for a language alone, such as pt,
    gets the language in any region, such as pt-BR. Chinese is matched by
    script: zh-TW, zh-HK and zh-MO read Traditional Chinese, and zh,
    zh-CN and zh-SG Simplified, and neither is offered the other.
    """
    choices = list(offered)
    wanted: list[tuple[float, str]] = []
    for part in header.split(","):
        tag, _, params = part.strip().partition(";")
        tag = tag.strip()
        if not tag or tag == "*":
            continue
        weight = 1.0
        if params.strip().startswith("q="):
            try:
                weight = float(params.strip()[2:])
            except ValueError:
                continue
        if weight > 0:
            wanted.append((weight, tag))
    for _weight, tag in sorted(wanted, key=lambda item: -item[0]):
        found = _match(tag, choices)
        if found is not None:
            return found
    return None


def direction(language: str) -> str:
    """Which way a language is written: rtl or ltr."""
    return "rtl" if _language(language) in RIGHT_TO_LEFT else "ltr"


def native_name(language: str) -> str:
    """What a language calls itself."""
    return NATIVE_NAMES.get(language, language)


def lower(text: str) -> str:
    """Text in small letters, by the rules of the current language.

    Turkish writes the capital of i as İ and the small letter of I as ı.
    """
    if _language(current_language.get()) in DOTTED_I:
        text = text.replace("İ", "i").replace("I", "ı")
    return text.lower()


def upper(text: str) -> str:
    """Text in capitals, by the rules of the current language."""
    if _language(current_language.get()) in DOTTED_I:
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()


def in_sentence(text: str) -> str:
    """A label as it reads inside a sentence: "Search order items".

    Each word goes into small letters, unless it is written in capitals,
    as an abbreviation such as API is. German keeps its capitals, since it
    writes every noun with one.
    """
    if _language(current_language.get()) in CAPITAL_NOUNS:
        return text
    return " ".join(
        word if len(word) > 1 and word.isupper() else lower(word)
        for word in text.split(" ")
    )


def activate(language: str, translations: Translations | None = None) -> None:
    """Make a language the one this request speaks."""
    current_language.set(language)
    _extra.set(translations or {})
