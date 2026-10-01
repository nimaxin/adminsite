"""One field of every kind, and an admin that registers the views."""

from typing import Literal

import pydantic
from sqlalchemy.ext.asyncio import create_async_engine

from adminsite import Admin, Link, ModelView, SaveContext
from adminsite.auth import hash_password
from adminsite.fields import (
    BooleanField,
    ComputedField,
    DateField,
    DateTimeField,
    DecimalField,
    EmailField,
    EnumField,
    FileField,
    FloatField,
    ImageField,
    IntegerField,
    JSONField,
    ListField,
    PasswordField,
    RelationField,
    StringField,
    TextAreaField,
    TimeField,
    UUIDField,
)
from adminsite.files import LocalStorage
from tests.reference.models import Promotion, Supplier, Tag

FILES = LocalStorage("uploads/promotions")


# Pydantic is imported as a module, so its Field and adminsite's never meet.
class PromotionSettings(pydantic.BaseModel):
    """What the settings column holds."""

    stackable: bool = False
    per_customer: int = pydantic.Field(default=1, ge=1, le=10)
    channel: Literal["web", "shop", "both"] = "both"


def runs_until(promotion: Promotion) -> str:
    return "" if promotion.ends_at is None else f"until {promotion.ends_at:%d %b}"


class SupplierView(ModelView[Supplier]):
    fields = [Supplier.name, Supplier.email, Supplier.country]
    record_title = "{name}"


class PromotionView(ModelView[Promotion]):
    fields = [
        # Each field starts from its column: the title's length and that it
        # may not be empty come from String(80), not from options here.
        StringField(Promotion.title),
        TextAreaField(Promotion.terms, help_text="Printed under the banner."),
        IntegerField(Promotion.uses_left),
        FloatField(Promotion.discount_rate, format="{:.0%}"),
        DecimalField(Promotion.minimum_spend, format="€{:,.2f}"),
        BooleanField(Promotion.active, tones={True: "green", False: None}),
        DateField(Promotion.starts_on),
        DateTimeField(Promotion.ends_at),
        TimeField(Promotion.daily_start),
        EnumField(Promotion.kind, tones="grey"),
        EnumField(Promotion.audience, choices=[("A", "Everyone"), ("M", "Members")]),
        EmailField(Promotion.contact_email),
        UUIDField(Promotion.public_id, read_only=True),
        JSONField(Promotion.settings, schema=PromotionSettings),
        ListField(Promotion.keywords),
        FileField(Promotion.terms_file, storage=FILES, accept=".pdf"),
        ImageField(Promotion.banner, storage=FILES),
        RelationField(Promotion.sponsor, view=SupplierView),
        Link(Promotion.sponsor, Supplier.email),
        Promotion.tags,
        ComputedField("runs_until", runs_until, label="Runs"),
        # Not a column: typed in the form, stored by the hook below as a hash.
        # secret_code_hash is left out of fields, so no page shows it.
        PasswordField("secret_code"),
    ]

    async def before_save(self, context: SaveContext[Promotion]) -> None:
        secret_code = context.values["secret_code"].get()
        if secret_code:
            context.record.secret_code_hash = hash_password(secret_code)


admin = Admin(
    create_async_engine("sqlite+aiosqlite://"),
    # A view with no settings needs no class of its own.
    views=[PromotionView, SupplierView, ModelView[Tag]],
)
