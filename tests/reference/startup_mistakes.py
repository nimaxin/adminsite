"""Mistakes no type checker can see, which the admin must refuse when it starts.

Every view here type-checks. Building an admin with it must fail, with a
message naming the view, the setting and the words in `EXPECTED`.
"""

from typing import Any

from pydantic import BaseModel

from adminsite import Field, Link, ModelView
from adminsite.actions import Selection, action
from adminsite.fields import ComputedField, DecimalField, EnumField, RelationField
from tests.reference.models import Customer, Order, Product


def line_count(order: Order) -> int:
    return len(order.items)


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


class TitleThroughALink(ModelView[Order]):
    record_title = "{customer.name}, #{id}"


class ExcludeForeignKey(ModelView[Order]):
    exclude_fields_from_list = [Order.customer_id]


class ExcludeNotShown(ModelView[Order]):
    fields = [Order.id, Order.status]
    exclude_fields_from_list = [Order.note]


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


class ChoiceOfNoMember(ModelView[Order]):
    fields = [EnumField(Order.status, choices=[("PIAD", "Paid")])]


class RelationOnForeignKey(ModelView[Order]):
    fields = [Order.id, RelationField(Order.customer_id)]


class RelationOnColumn(ModelView[Order]):
    fields = [Order.id, RelationField("note")]


class KindOfAnotherType(ModelView[Customer]):
    fields = [DecimalField("name")]


class LengthOfADate(ModelView[Order]):
    fields = [Field(Order.created_at, max_length=5)]


class LengthOfARelation(ModelView[Order]):
    fields = [RelationField(Order.customer, max_length=3)]


class RequiredComputed(ModelView[Order]):
    fields = [Order.id, ComputedField("lines", line_count, required=True)]


class InputNobodyCanDraw(ModelView[Order]):
    @action("Ship")
    async def ship(self, selection: Selection[Order], *, carrier: Carrier) -> str:
        return ""


# The words each refusal must contain, besides the view's name. Views of
# different models share the dict, which is the boundary where Any belongs.
class RefreshNever(ModelView[Order]):
    list_refresh_seconds = 0


EXPECTED: dict[type[ModelView[Any]], list[str]] = {
    MisspeltString: ['"totl"', "total"],
    BareString: ["searchable_fields", "list"],
    ColumnOfAnotherModel: ["Customer.name", "Order"],
    LinkThatLeadsElsewhere: ["Product.name", "Customer"],
    NotAModel: ["OrderSchema", "mapped"],
    TitleTypo: ["{nmae}", "record_title"],
    TitleThroughALink: ["record_title", '"customer"', "own columns"],
    ExcludeForeignKey: ["exclude_fields_from_list", '"customer_id"', '"customer"'],
    ExcludeNotShown: ["exclude_fields_from_list", '"note"', "id, status"],
    ExcludedAndHidden: ["note", "hidden_in_list", "exclude_from_list"],
    ExcludedByListAndHidden: ["note", "hidden_in_list", "exclude_fields_from_list"],
    ChoicesMissing: ["region", "choices"],
    ToneForUnknownValue: ['"US"', "tones"],
    ChoiceOfNoMember: ['"PIAD"', "OrderStatus", "PAID"],
    RelationOnForeignKey: ["Order.customer_id", "RelationField(Order.customer)"],
    RelationOnColumn: ['RelationField("note")', "customer, items"],
    KindOfAnotherType: ['DecimalField("name")', "Customer.name holds str"],
    LengthOfADate: ["DateTimeField", "max_length=5"],
    LengthOfARelation: ["RelationField(Order.customer)", "max_length=3"],
    RequiredComputed: ['ComputedField("lines")', "required=True"],
    InputNobodyCanDraw: ["carrier", "Carrier"],
    RefreshNever: ["list_refresh_seconds is 0", "whole number of seconds"],
}
