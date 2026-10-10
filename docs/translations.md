# Translations

The admin speaks English unless you choose another language. These ship with it, each translated
in full, and the layout mirrors for languages written right to left:

| Language | `language` | A browser set to it gets it |
| --- | --- | --- |
| English | `en` | `en`, `en-US`, `en-GB` and any other English |
| فارسی, Persian | `fa` | `fa`, `fa-IR` |
| Русский, Russian | `ru` | `ru`, `ru-RU` |
| Español, Spanish | `es` | `es`, `es-ES`, `es-MX`, `es-419` and any other Spanish |
| Português (Brasil), Brazilian Portuguese | `pt-BR` | `pt-BR`, `pt`, and `pt-PT` until European Portuguese ships |
| 日本語, Japanese | `ja` | `ja`, `ja-JP` |
| Deutsch, German | `de` | `de`, `de-DE`, `de-AT`, `de-CH` |
| Français, French | `fr` | `fr`, `fr-FR`, `fr-CA`, `fr-BE` |
| Türkçe, Turkish | `tr` | `tr`, `tr-TR` |
| العربية, Arabic | `ar` | `ar`, `ar-SA`, `ar-EG`, `ar-AE` and any other Arabic |
| 简体中文, Simplified Chinese | `zh-Hans` | `zh-CN`, `zh-SG`, `zh` and `zh-Hans`; never `zh-TW`, `zh-HK` or `zh-MO` |
| 繁體中文, Traditional Chinese | `zh-Hant` | `zh-TW`, `zh-HK`, `zh-MO` and `zh-Hant`; never `zh-CN`, `zh-SG` or `zh` |

```python
admin = Admin(engine, views=[OrderView], language="fa")
```

In Persian or Arabic, every page reads from right to left, with the sidebar on the right.

The examples on this page use the [example models](models.md).

## Let people choose

Offer more than one language, and the account menu at the foot of the sidebar lets people choose:

```python
admin = Admin(engine, language="en", languages=["fa"])
```

The first visit follows the browser's `Accept-Language`, and a choice made in the menu is kept for
a year. `language` is the default for a browser that asks for neither.

adminsite translates its own texts, not yours: your labels stay as you wrote them in every
language. Offer several languages where your team can read your labels in each.

## Your own words

Your views' labels, groups and field labels are yours, so write them in the language you want:

```python
class OrderView(ModelView[Order]):
    label = "سفارش"
    label_plural = "سفارش‌ها"
    fields = [Order.id, EnumField(Order.status, label="وضعیت", choices=STATUSES)]
```

To change one of adminsite's own texts, or to add a language it does not ship, pass
`translations`, keyed by the English texts:

```python
admin = Admin(
    engine,
    language="it",
    translations={
        "it": {
            "Save": "Salva",
            "Cancel": "Annulla",
            "Search {things}": "Cerca {things}",
        },
        "fa": {"Save": "ثبت"},
    },
)
```

Your translations win over the ones adminsite ships, and a text with no translation stays in
English. Keep the `{placeholders}` as they are, though their place in the sentence may move.

A text with a count has a translation for each plural category of its language, as the Unicode
CLDR names them, such as one, few and many for Russian. It is keyed by the English for one:

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

English works the same way, so one of the admin's words can change without copying a template, as
for a project that signs people in with their email address:

```python
admin = Admin(engine, auth=auth, translations={"en": {"Username": "Email"}})
```

In your own code, such as a hook's refusal or an action's message, use the same function, and add
the text to `translations`:

```python
from adminsite.i18n import gettext as _

raise RefusedError(_("Shipped orders cannot be changed."))
```

For a text with a count, give the English for one and for any other number, and the count:

```python
from adminsite.i18n import ngettext


def shipped(count: int) -> str:
    return ngettext("{count} order shipped.", "{count} orders shipped.", count)
```

## Time zones

Every time is shown on the clock of the person reading it, in the time zone their browser is set
to, and a time typed in is read on the same clock. Dates and times of day stay as they are.

Until the browser has said which zone it is in, and for a request with an API token, times are in
`timezone`, which is UTC unless you choose. More zones listed in `timezones` show in the account
menu:

```python
admin = Admin(engine, timezone="Asia/Tehran", timezones=["Europe/Paris", "Asia/Tehran"])
```

A datetime the database keeps without a zone is taken to be in UTC. A database that keeps local
times says which zone:

```python
admin = Admin(engine, database_timezone="Asia/Tehran")
```

A column declared `DateTime(timezone=True)` keeps its zone. In your own code, such as a dashboard
card that counts today's orders, `current_timezone` is the zone of the person reading:

```python
from datetime import datetime

from adminsite.timezones import current_timezone

today = datetime.now(current_timezone.get()).date()
```

## Add a language to adminsite

The catalogs live in `src/adminsite/locales`, one JSON file per language. To list every text a
catalog is missing:

```bash
uv run python -m tests.messages de
```

It prints a JSON object of the missing texts, ready to fill in, with a place for each plural form.
The test suite checks that every shipped catalog translates every text and keeps every
placeholder.
