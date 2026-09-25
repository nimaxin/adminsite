# Translations

The admin speaks English unless you choose another language. Persian ships with it, and the
layout mirrors for languages written right to left.

```python
admin = Admin(engine, views=[OrderView], language="fa")
```

Every page then has `<html lang="fa" dir="rtl">`: the sidebar moves to the right, tables and
toolbars read from right to left, and the pager's "previous" sits on the right. Buttons, messages,
form errors, refusals, filter shortcuts and the history all come in Persian.

## Letting people choose

Offer more than one language and a menu appears at the foot of the sidebar:

```python
admin = Admin(engine, language="en", languages=["fa"])
```

The first visit follows the browser's `Accept-Language`, so someone whose browser prefers Persian
gets Persian. A choice made in the menu is kept in a cookie for a year. `language` stays the
default for anyone whose browser asks for neither.

## Your own words

Your views' labels, groups and field labels are yours, so write them in the language you want:

```python
class OrderView(ModelView, model=Order):
    label = "سفارش"
    label_plural = "سفارش‌ها"
    fields = (ChoiceField("status", label="وضعیت", choices=STATUSES),)
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

and add the text to `translations`.

## Adding a language to adminsite

The catalogs live in `src/adminsite/locales`, one JSON file per language. To list every text a
catalog is missing:

```bash
uv run python -m tests.messages de
```

It prints a JSON object of the missing texts, ready to fill in. The test suite checks that the
shipped catalogs translate every text and keep every placeholder.
