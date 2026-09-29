"""A name 0.1.0a10 changed is refused with the name it has now.

The settings and methods of a view are in reference/startup_mistakes.py, and
the keywords a type checker refuses in reference/mistakes.py.
"""

import importlib

import pytest

from adminsite import AdminSiteError
from adminsite.fields import StringField


@pytest.mark.parametrize(
    ("module", "old", "new"),
    [
        ("adminsite", "Computed", "ComputedField"),
        ("adminsite", "FieldOptions", "Field"),
        ("adminsite.fields", "ChoiceField", "EnumField"),
        ("adminsite.fields", "Computed", "ComputedField"),
        ("adminsite.fields", "FieldOptions", "Field"),
        ("adminsite.fields", "TextField", "TextAreaField"),
        ("adminsite.auth", "SignInRefused", "SignInRefusedError"),
        ("adminsite.exceptions", "SignInRefused", "SignInRefusedError"),
    ],
)
def test_an_old_name_says_what_to_import(module: str, old: str, new: str) -> None:
    with pytest.raises(ImportError, match=f"{old} is called {new} now"):
        getattr(importlib.import_module(module), old)


def test_a_name_that_never_existed_is_missing_as_usual() -> None:
    fields = importlib.import_module("adminsite.fields")

    with pytest.raises(AttributeError):
        fields.Nothing  # noqa: B018  # read for the error it raises


def test_a_field_class_keeping_its_value_by_the_old_name_is_refused() -> None:
    with pytest.raises(AdminSiteError, match="called keeps_value_when_blank now"):

        class Secret(StringField):
            blank_keeps = True
