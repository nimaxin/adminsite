# Translations

The admin speaks English unless you choose another language. The ones below ship with it, and the
layout mirrors for languages written right to left.

| Language | `language` | A browser set to it gets it |
| --- | --- | --- |
| English | `en` | `en`, `en-US`, `en-GB` and any other English |
| فارسی, Persian | `fa` | `fa`, `fa-IR` |
| Русский, Russian | `ru` | `ru`, `ru-RU` |

Each one is translated in full, texts with a count in every form the language writes a number in,
dates with its own month names, and lists, counts and the history in its own order and punctuation.

```python
admin = Admin(engine, views=[OrderView], language="fa")
```

Every page then has `<html lang="fa" dir="rtl">`: the sidebar moves to the right, tables and
toolbars read from right to left, and the pager's "previous" sits on the right. Buttons, messages,
form errors, refusals, filter shortcuts and the history all come in Persian.

## Letting people choose

Offer more than one language and the account menu, at the foot of the sidebar, lets people choose:

```python
admin = Admin(engine, language="en", languages=["fa"])
```

The first visit follows the browser's `Accept-Language`, so someone whose browser prefers Persian
gets Persian. A choice made in the menu is kept in a cookie for a year. `language` stays the
default for anyone whose browser asks for neither.

adminsite translates its own texts, not yours: your views' labels, groups and field labels stay as
you wrote them, whichever language is chosen. Offer several languages where your team reads them
all, and otherwise keep the admin in the one your labels are written in.

## Your own words

Your views' labels, groups and field labels are yours, so write them in the language you want:

```python
class OrderView(ModelView[Order]):
    label = "سفارش"
    label_plural = "سفارش‌ها"
    fields = [Order.id, EnumField(Order.status, label="وضعیت", choices=STATUSES)]
```

To change one of adminsite's own texts, or to add a language adminsite does not ship, pass
`translations`. The keys are the English texts:

```python
admin = Admin(
    engine,
    language="de",
    translations={
        "de": {
            "Save": "Speichern",
            "Cancel": "Abbrechen",
            "Search {things}": "{things} suchen",
        },
        "fa": {"Save": "ثبت"},
    },
)
```

Your translations win over the ones adminsite ships, and a text with no translation stays in
English rather than disappearing. Keep the `{placeholders}` as they are; only their place in the
sentence may move.

A text with a count has a translation for each form its language writes a number in. English has
two, "1 row" and "2 rows", and Russian three, so the translation is keyed by the English for one,
and holds a text for each plural category:

```python
admin = Admin(
    engine,
    language="ru",
    translations={
        "ru": {
            "{count} row": {
                "one": "{count} строка",
                "few": "{count} строки",
                "many": "{count} строк",
            },
        },
    },
)
```

A count is written with its thousands set apart as the language does, 1.234 in German, and a list
inside a sentence with the language's own separator, the translation of ", ".

The categories are the ones the Unicode CLDR names for each language: one and other for English,
German, Spanish, French, Portuguese, Turkish and Persian; one, few and many for Russian; zero, one,
two, few, many and other for Arabic. Japanese and Chinese write every number the same way, so their
translation is plain text.

English works the same way, so a word of the admin's own can change without copying the template
it is in. A project that signs people in with their email address:

```python
admin = Admin(engine, auth=auth, translations={"en": {"Username": "Email"}})
```

In your own code, such as a hook's refusal or an action's message, use the same function:

```python
from adminsite.i18n import gettext as _

raise RefusedError(_("Shipped orders cannot be changed."))
```

and add the text to `translations`. For a text with a count, give the English for one and for any
other number, and the count:

```python
from adminsite.i18n import ngettext


def shipped(count: int) -> str:
    return ngettext("{count} order shipped.", "{count} orders shipped.", count)
```

## Time zones

Every time is shown on the clock of the person reading it. The browser says which time zone it is
set to, and each datetime is shown in that zone: in the list, on the record page, in the form, the
history and the export. A time typed into a form, the list, an import or the API is read in the
same zone. Dates and times of day stay as they are.

Until the browser has said, on the first page of a first visit and for a request with an API
token, times are shown in `timezone`, which is UTC unless you choose:

```python
admin = Admin(engine, timezone="Asia/Tehran")
```

List more zones and the account menu offers them, below the language:

```python
admin = Admin(engine, timezones=["Europe/Paris", "Asia/Tehran"])
```

The menu names each zone by its city and how far it is from UTC, such as Tehran, UTC+03:30, and
offers the browser's own zone first, so choosing it follows the browser again. A choice is kept in a cookie
for a year.

### How the database keeps times

A datetime the database keeps without a zone is taken to be in UTC, as
`datetime.now(UTC).replace(tzinfo=None)` writes it. A project whose database keeps local times
says which zone:

```python
admin = Admin(engine, database_timezone="Asia/Tehran")
```

A column declared `DateTime(timezone=True)` keeps its zone, so it is given a time with one, and
Postgres stores the moment meant whatever zone its connection is set to.

A time the clocks skip or repeat when they change, such as 02:30 on the night Paris moves its
clocks forward, is refused with a message, since it names no one moment.

The audit log writes times in UTC, whoever made the change, and the history says so beside a
time that changed. The API sends each datetime in UTC with its offset, such as
`2026-09-01T10:30:00Z`.

In your own code, such as a dashboard card that counts today's orders, the zone of the person
reading is `current_timezone`:

```python
from datetime import datetime

from adminsite.timezones import current_timezone

today = datetime.now(current_timezone.get()).date()
```

## Adding a language to adminsite

The catalogs live in `src/adminsite/locales`, one JSON file per language. To list every text a
catalog is missing:

```bash
uv run python -m tests.messages de
```

It prints a JSON object of the missing texts, ready to fill in, with a place for each plural form
of a text with a count. The test suite checks that the shipped catalogs translate every text, in
every form, and keep every placeholder. A translation may leave out `{thing}` or `{things}`, the
name of the records, where its grammar has no room for a name it cannot change. A form that is for
one number only, such as Arabic's two, may leave out the number, as "صفان" does.
