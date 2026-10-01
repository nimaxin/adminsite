"""Mistakes in hooks and actions the type checker must refuse, one to a line.

The mistakes in a view's fields and settings are in field_mistakes.py.

Each line carries the error mypy has to report there. mypy strict turns an
ignore it no longer needs into an error of its own, so a mistake that stops
being caught fails the check. tests/test_type_mistakes.py reads each line
again without its ignore and checks mypy's message, so a line refused for
another reason fails too.

One kind of mistake is missing on purpose: mypy does not check a call written
inside Annotated[...], so Input(lable="Note") in a parameter's annotation is not
a mypy error. pyright reports it as you type, and Python raises TypeError when
the module is imported, so it never reaches a running admin.
"""

from sqlalchemy import select
from starlette.requests import Request

from adminsite import (
    ModelView,
    SaveContext,
    Statement,
)
from adminsite.actions import Selection, action
from tests.reference.models import Customer, Order


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
