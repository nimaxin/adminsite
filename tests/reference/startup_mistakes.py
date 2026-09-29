"""Mistakes no type checker can see, which the admin must refuse when it starts.

Every view here type-checks. Building an admin with it must fail, with a
message naming the view, the setting and the words in `EXPECTED`.
"""

from typing import Any

from pydantic import BaseModel

from adminsite import Field, Link, ModelView
from adminsite.actions import Selection, action
from adminsite.fields import EnumField
from tests.reference.models import Customer, Order, Product


class OrderSchema(BaseModel):
    """What a FastAPI app sends: easy to pass instead of the model."""

    id: int
    total: float


class Carrier:
    """A plain class, which no input can be drawn for."""


class MisspeltString(ModelView[Order]):
    fields = [Order.id, "totl"]


class BareString(ModelView[Order]):
    searchable_fields = "note"


class ColumnOfAnotherModel(ModelView[Order]):
    fields = [Order.id, Customer.name]


class LinkThatLeadsElsewhere(ModelView[Order]):
    fields = [Order.id, Link(Order.customer, Product.name)]


class NotAModel(ModelView[OrderSchema]):
    pass


class TitleTypo(ModelView[Order]):
    record_title = "Order #{nmae}"


class ExcludedAndHidden(ModelView[Order]):
    fields = [Field(Order.note, exclude_from_list=True, hidden_in_list=True)]


class ExcludedByListAndHidden(ModelView[Order]):
    fields = [Field(Order.note, hidden_in_list=True)]
    exclude_fields_from_list = [Order.note]


class ChoicesMissing(ModelView[Customer]):
    fields = [EnumField(Customer.region)]


class ToneForUnknownValue(ModelView[Customer]):
    fields = [
        EnumField(Customer.region, choices=[("EU", "Europe")], tones={"US": "blue"})
    ]


class InputNobodyCanDraw(ModelView[Order]):
    @action("Ship")
    async def ship(self, selection: Selection[Order], *, carrier: Carrier) -> str:
        return ""


# The words each refusal must contain, besides the view's name. Views of
# different models share the dict, which is the boundary where Any belongs.
EXPECTED: dict[type[ModelView[Any]], list[str]] = {
    MisspeltString: ['"totl"', "total"],
    BareString: ["searchable_fields", "list"],
    ColumnOfAnotherModel: ["Customer.name", "Order"],
    LinkThatLeadsElsewhere: ["Product.name", "Customer"],
    NotAModel: ["OrderSchema", "mapped"],
    TitleTypo: ["{nmae}", "record_title"],
    ExcludedAndHidden: ["note", "hidden_in_list", "exclude_from_list"],
    ExcludedByListAndHidden: ["note", "hidden_in_list", "exclude_fields_from_list"],
    ChoicesMissing: ["region", "choices"],
    ToneForUnknownValue: ['"US"', "tones"],
    InputNobodyCanDraw: ["carrier", "Carrier"],
}
