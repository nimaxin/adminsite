"""Mistakes the type checker must refuse, one to a line.

Each line carries the error mypy has to report there. mypy strict turns an
ignore it no longer needs into an error of its own, so a mistake that stops
being caught fails the check.

One kind of mistake is missing on purpose: mypy does not check a call written
inside Annotated[...], so Input(lable="Note") in a parameter's annotation is not
a mypy error. pyright reports it as you type, and Python raises TypeError when
the module is imported, so it never reaches a running admin.
"""

from sqlalchemy import select
from starlette.requests import Request

from adminsite import Descending, Field, ModelView, SaveContext, Statement
from adminsite.actions import Selection, action
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


class WrongHooks(ModelView[Order]):
    def get_record_title(self, record: Customer, /) -> str:  # type: ignore[override]
        return record.name

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return select(Order)  # type: ignore[return-value]  # a different query

    async def before_save(self, context: SaveContext[Order]) -> None:
        context.values[Order.total].set("a lot")  # type: ignore[arg-type]
        context.values[Order.totl].set(0)  # type: ignore[attr-defined]


class WrongActions(ModelView[Order]):
    @action("Oops", on="records")  # type: ignore[arg-type]  # no such target
    async def oops(self, selection: Selection[Order]) -> str:
        return ""
