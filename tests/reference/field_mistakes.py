"""Mistakes in a view's fields the type checker must refuse, one to a line.

Each line carries the error mypy has to report there. mypy strict turns an
ignore it no longer needs into an error of its own, so a mistake that stops
being caught fails the check.
"""

from adminsite import Descending, Field, ModelView
from adminsite.fields import ComputedField, EnumField, TextAreaField
from tests.reference.models import Customer, Order, OrderStatus


def customer_name(customer: Customer) -> str:
    return customer.name


class WrongFields(ModelView[Order]):
    fields = [
        TextAreaField(Order.total),  # type: ignore[arg-type]  # text on a Decimal
        Field(Order.note, lable="Note"),  # type: ignore[call-arg]  # misspelt option
        Field(Order.note, "Note"),  # type: ignore[call-arg]  # option by position
        Field(Order.note, read_only="yes"),  # type: ignore[arg-type]  # not a bool
        EnumField(Order.status, tones={OrderStatus.PAID: "pink"}),  # type: ignore[dict-item]
        Order.totl,  # type: ignore[attr-defined]  # no such column
        ComputedField("name", customer_name),  # type: ignore[arg-type]  # another model
    ]


class WrongSettings(ModelView[Order]):
    fields_default_sort = [Order.created_at.desc()]  # type: ignore[list-item]  # SQL
    exclude_fields_from_list = [Order.totl]  # type: ignore[attr-defined]
    sortable_fields = [Descending(Order.total)]  # type: ignore[list-item]  # a sort, not a column


class NotAList(ModelView[Order]):
    fields = Order.id  # type: ignore[assignment]  # one column where a list belongs
