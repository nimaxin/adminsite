"""An order view with what a view usually has."""

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from adminsite import (
    ColumnReference,
    DeleteContext,
    Descending,
    Field,
    Inline,
    Link,
    Message,
    ModelView,
    Permission,
    RefusedError,
    RequestAction,
    SaveContext,
    Statement,
)
from adminsite.actions import Input, Selection, action
from adminsite.fields import ComputedField, DecimalField, EnumField, TextAreaField
from tests.reference.models import Customer, Order, OrderItem, OrderStatus

EUROS = "€{:,.2f}"


def is_manager(request: Request) -> bool:
    return bool(request.session.get("manager"))


def line_count(order: Order) -> int:
    return len(order.items)


async def send_confirmation(order: Order) -> None:
    """Stands in for an email to the customer."""


async def send_cancellation(order: Order) -> None:
    """Stands in for an email to the customer."""


class CustomerView(ModelView[Customer]):
    fields = [Customer.name, Customer.email, Customer.region, Customer.is_active]
    searchable_fields = [Customer.name, Customer.email]
    record_title = "{name} ({email})"


class OrderView(ModelView[Order]):
    fields = [
        Order.id,
        # The list shows the customer's record title, the form a picker.
        Order.customer,
        # One column of the related model: shown, never edited.
        Link(Order.customer, Customer.email),
        EnumField(
            Order.status,
            tones={
                OrderStatus.PENDING: "amber",
                OrderStatus.PAID: "green",
                OrderStatus.SHIPPED: "blue",
                OrderStatus.REFUNDED: "rose",
            },
        ),
        DecimalField(Order.total, format=EUROS, read_only=True),
        TextAreaField(Order.note, exclude_from_list=True),
        Field(
            Order.created_at,
            label="Placed",
            hidden_in_list=True,
            exclude_from_create=True,
            exclude_from_edit=True,
        ),
        ComputedField("lines", line_count, label="Lines", needs=[Order.items]),
    ]
    searchable_fields = [
        Order.id,
        Link(Order.customer, Customer.name),
        Link(Order.customer, Customer.email),
    ]
    sortable_fields = [Order.created_at, Order.total]
    fields_default_sort = [Descending(Order.created_at)]
    list_filters = [
        Order.status,
        Order.created_at,
        Link(Order.customer, Customer.region),
    ]
    inlines = [
        Inline(
            Order.items,
            fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
            blank_rows=1,
        )
    ]
    record_title = "Order #{id}"
    page_size = 50
    page_size_options = [25, 50, 100]

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        """Staff with a region see only that region's orders."""
        region = request.session.get("region")
        if region is None:
            return statement
        return statement.where(Order.customer.has(Customer.region == region))

    def search_condition(
        self, term: str, *, request: Request
    ) -> ColumnElement[bool] | None:
        """An order number typed with its hash, such as #1042, finds that order."""
        digits = term.removeprefix("#")
        return Order.id == int(digits) if digits.isdigit() else None

    def can_access_field(
        self, request: Request, field: Field[Any], action: RequestAction
    ) -> bool:
        return field.name != "total" or is_manager(request)

    def get_readonly_fields(
        self, request: Request, record: Order | None
    ) -> Sequence[ColumnReference]:
        """A shipped order keeps its customer and its status."""
        if record is not None and record.status is OrderStatus.SHIPPED:
            return [Order.customer, Order.status]
        return []

    async def before_save(self, context: SaveContext[Order]) -> None:
        paid = context.values[Order.status].get() is OrderStatus.PAID
        if paid and not context.values[Order.note].get():
            raise RefusedError(
                "Say why the order was marked paid by hand.", field=Order.note
            )

    async def after_save_committed(self, context: SaveContext[Order]) -> None:
        if context.created:
            await send_confirmation(context.record)

    async def after_delete_committed(self, context: DeleteContext[Order]) -> None:
        await send_cancellation(context.record)

    @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
    async def mark_shipped(self, selection: Selection[Order]) -> str:
        orders = await selection.records()
        for order in orders:
            order.status = OrderStatus.SHIPPED
        return f"{len(orders)} orders marked as shipped."

    @action("Refund", on="record", dangerous=True, confirm="Refund this order?")
    async def refund(
        self,
        order: Order,
        request: Request,
        *,
        reason: Annotated[str, Input(label="Reason", multiline=True)],
    ) -> Message:
        order.status = OrderStatus.REFUNDED
        order.note = f"{reason} (refunded by {request.session.get('user', 'staff')})"
        return Message(f"Order #{order.id} is refunded.", sticky=True)

    @action("Export one day", on="view", permission=Permission.EXPORT)
    async def export_day(
        self, session: AsyncSession, request: Request, *, day: date
    ) -> Response:
        # An action's own query goes through scope_query, so a region's staff
        # export only their region's orders, as they see only those in the list.
        statement = self.scope_query(
            select(Order).where(func.date(Order.created_at) == day), request=request
        )
        orders = await session.scalars(statement)
        lines = [f"{order.id},{order.total}" for order in orders]
        return Response(
            "\n".join(lines),
            media_type="text/csv",
            headers={"content-disposition": f'attachment; filename="orders-{day}.csv"'},
        )
