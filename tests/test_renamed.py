"""An old name is refused with the name adminsite uses.

The settings and methods of a view are in reference/startup_mistakes.py, and
the keywords a type checker refuses in reference/mistakes.py.
"""

import importlib
from collections.abc import Callable
from functools import partial

import pytest
from sqlalchemy import create_engine

from adminsite import Admin, AdminSiteError, Field, Inline, Permission, RecentRecords
from adminsite.fields import DecimalField, EnumField, RelationField, StringField
from tests.models import Order


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
    with pytest.raises(ImportError, match=f"calls it {new}, not {old}"):
        getattr(importlib.import_module(module), old)


def test_a_name_that_never_existed_is_missing_as_usual() -> None:
    fields = importlib.import_module("adminsite.fields")

    with pytest.raises(AttributeError):
        fields.Nothing  # noqa: B018  # read for the error it raises


def test_a_field_class_keeping_its_value_by_the_old_name_is_refused() -> None:
    with pytest.raises(AdminSiteError, match="calls keeps_value_when_blank"):

        class Secret(StringField):
            blank_keeps = True


def test_the_old_permission_names_the_new_one() -> None:
    with pytest.raises(AttributeError, match="calls it VIEW_DETAIL, not DETAIL"):
        # Read for the error it raises, which a type checker reports as missing.
        Permission.DETAIL  # type: ignore[attr-defined]  # noqa: B018

    assert not hasattr(Permission, "NOTHING")
    assert Permission("detail") is Permission.VIEW_DETAIL


@pytest.mark.parametrize(
    ("build", "old", "new"),
    [
        (partial(Inline, Order.items), "extra", "blank_rows"),
        (partial(Inline, Order.items), "display_template", "record_title"),
        (Inline, "name", "relation"),
        (partial(RelationField, Order.customer), "display_template", "record_title"),
        (partial(EnumField, Order.status), "enum_class", "enum"),
        (partial(Field, Order.note), "readonly", "read_only"),
        (partial(DecimalField, Order.total), "readonly", "read_only"),
        (partial(RecentRecords, "Latest", "orders"), "detail", "value"),
        (partial(Admin, create_engine("sqlite://")), "fields", "field_registry"),
    ],
)
def test_an_old_keyword_names_the_new_one(
    build: Callable[..., object], old: str, new: str
) -> None:
    called = getattr(build, "func", build).__name__

    with pytest.raises(TypeError) as raised:
        build(**{old: None})

    assert str(raised.value) == (
        f"{called} calls it {new}, not {old}. Write {new}= instead."
    )


def test_a_keyword_with_no_new_name_says_what_to_write() -> None:
    build: Callable[..., object] = Inline

    with pytest.raises(TypeError) as raised:
        build(Order.items, readonly_fields=["unit_price"])

    assert str(raised.value) == (
        "Inline takes no readonly_fields. Give each of those fields read_only=True "
        "in fields."
    )


def test_the_new_keywords_are_taken() -> None:
    inline = Inline(Order.items, blank_rows=2, record_title="{quantity}")

    assert (inline.name, inline.blank_rows, inline.record_title) == (
        "items",
        2,
        "{quantity}",
    )
    assert DecimalField(Order.total, read_only=True).read_only is True
    assert RecentRecords("Latest", "orders", value="total").value == "total"
