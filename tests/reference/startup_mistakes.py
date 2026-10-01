"""Mistakes no type checker can see, which the admin must refuse when it starts.

Every view here type-checks. Building an admin with it must fail, with a
message naming the view, the setting and the words in `EXPECTED`.
"""

from typing import Any

from pydantic import BaseModel
from starlette.requests import Request

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


# Names 0.1.0a10 changed, each written the old way. Python takes an old name
# as one more attribute, which adminsite would never read.


class OldListFilter(ModelView[Order]):
    list_filter = [Order.status]


class OldPageSizes(ModelView[Order]):
    page_sizes = [25, 50]


class OldBulkDelete(ModelView[Order]):
    bulk_delete = False


class OldSearchFields(ModelView[Order]):
    search_fields = [Order.note]


class OldOrdering(ModelView[Order]):
    ordering = ["-created_at"]


class OldDisplayTemplate(ModelView[Order]):
    display_template = "Order #{id}"


class OldCanDetail(ModelView[Order]):
    can_detail = False


class OldGetFilters(ModelView[Order]):
    def get_filters(self, request: Request) -> list[str]:
        return ["status"]


class OldGetSearchFields(ModelView[Order]):
    def get_search_fields(self, request: Request) -> list[str]:
        return ["note"]


class OldGetOrdering(ModelView[Order]):
    def get_ordering(self, request: Request) -> list[str]:
        return ["-created_at"]


class OldTitleOf(ModelView[Order]):
    def title_of(self, record: Order) -> str:
        return f"Order #{record.id}"


class OldFormValues(ModelView[Order]):
    async def form_values(self, session: Any, record: Any, *, request: Any) -> Any:
        return {}


class OldDeleteSelected(ModelView[Order]):
    async def delete_selected(self, selection: Selection[Order]) -> str:
        return "Nothing deleted."


class OldListDisplay(ModelView[Order]):
    list_display = ["id", "total"]


class OldListColumns(ModelView[Order]):
    list_columns = ["note"]


class OldFormFields(ModelView[Order]):
    form_fields = ["customer", "status"]


class OldDetailFields(ModelView[Order]):
    detail_fields = ["customer", "total"]


class OldExclude(ModelView[Order]):
    exclude = ["note"]


class OldReadonlyFields(ModelView[Order]):
    readonly_fields = ["total"]


class OldGetListDisplay(ModelView[Order]):
    def get_list_display(self, request: Request) -> list[str]:
        return ["id"]


class OldGetFormFields(ModelView[Order]):
    def get_form_fields(self, request: Request, record: Order | None) -> list[str]:
        return ["status"]


class OldGetDetailFields(ModelView[Order]):
    def get_detail_fields(self, request: Request, record: Order | None) -> list[str]:
        return ["status"]


class OldGetColumnChoices(ModelView[Order]):
    def get_column_choices(self, request: Request) -> list[str]:
        return ["id"]


class OldGetPageSizes(ModelView[Order]):
    def get_page_sizes(self, request: Request) -> list[int]:
        return [99]


# The words each refusal must contain, besides the view's name. Views of
# different models share the dict, which is the boundary where Any belongs.
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
    OldListFilter: ["list_filter", "list_filters"],
    OldPageSizes: ["page_sizes", "page_size_options"],
    OldBulkDelete: ["bulk_delete", "can_delete_selected"],
    OldSearchFields: ["search_fields", "searchable_fields"],
    OldOrdering: ["ordering", "fields_default_sort"],
    OldDisplayTemplate: ["display_template", "record_title"],
    OldCanDetail: ["can_detail", "can_view_detail"],
    OldGetFilters: ["get_filters", "get_list_filters"],
    OldGetSearchFields: ["get_search_fields", "get_searchable_fields"],
    OldGetOrdering: ["get_ordering", "get_fields_default_sort"],
    OldTitleOf: ["title_of", "get_record_title"],
    OldFormValues: ["form_values", "form_only_values"],
    OldDeleteSelected: ["delete_selected", "can_delete_selected = False"],
    OldListDisplay: ["sets list_display", "exclude_fields_from_list"],
    OldListColumns: ["sets list_columns", "hidden_in_list=True"],
    OldFormFields: ["sets form_fields", "exclude_fields_from_create"],
    OldDetailFields: ["sets detail_fields", "exclude_fields_from_detail"],
    OldExclude: ["sets exclude", "leaving those out"],
    OldReadonlyFields: ["sets readonly_fields", "read_only=True"],
    OldGetListDisplay: ["defines get_list_display", "can_access_field"],
    OldGetFormFields: ["defines get_form_fields", "can_access_field"],
    OldGetDetailFields: ["defines get_detail_fields", "can_access_field"],
    OldGetColumnChoices: ["defines get_column_choices", "can_access_field"],
    OldGetPageSizes: ["defines get_page_sizes", "page_size_options"],
}
