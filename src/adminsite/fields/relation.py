from collections.abc import Iterable
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any, Self

from adminsite.exceptions import AdminSiteError, FieldValidationError
from adminsite.fields.base import Field
from adminsite.i18n import gettext as _
from adminsite.schema import RelationSchema
from adminsite.text import RecordValues

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = [
    "RelationField",
]


@dataclass(eq=False, repr=False)
class RelationField(Field[Any]):
    """A link to one or many other records: `RelationField(Order.customer)`.

    The relationship says which model it links to and whether it holds many
    records. Every option of `BaseField` applies as well, but `max_length`.

    Args:
        column: The relationship, such as `Order.customer`, or its name.
        target: The model of the linked records. The view fills it in from
            the relationship.
        collection: Whether it links to many records. The view fills it in
            from the relationship.
        record_title: How each linked record is named here, such as
            `"{name} ({email})"`. Left out, as the linked view names it.
        view: The view its links open and its picker lists from, as a class
            or by its name, for a model shown by more than one view. Left
            out, the first view registered for that model.
        ordered: For a link to many whose order means something, such as
            servers tried in turn. The form can put its records in order,
            and saving writes the link again in that order when it changed.

    Raises:
        AdminSiteError: When `ordered` is given for a link to one record.
    """

    _: KW_ONLY
    target: type[Any] | None = None
    collection: bool = False
    record_title: str | None = None
    view: "type[ModelView[Any]] | str | None" = None
    ordered: bool = False

    widget = "relation"
    python_type = str
    error_message = "Choose a record."
    unused_options = frozenset({"max_length"})

    def __post_init__(self) -> None:
        super().__post_init__()
        # A field waiting for its relationship does not know yet whether it
        # links to many; the check runs again once the view fills it in.
        if self.ordered and self.target is not None and not self.collection:
            raise AdminSiteError(
                f"The field {self.name!r} links to one record, so it has no order: "
                "ordered=True is for a link to many."
            )

    @classmethod
    def from_relation(cls, schema: RelationSchema, **overrides: Any) -> Self:
        """Build the field for an inspected relationship."""
        return cls(schema.name, **overrides).filled_from_relation(schema)

    def filled_from_relation(self, schema: RelationSchema) -> Self:
        """A copy with what the relationship says wherever no option was given."""
        options: dict[str, Any] = {
            "target": schema.target,
            "collection": schema.collection,
        }
        if not self.labelled:
            options["label"] = schema.label
        if self.required is None:
            options["required"] = not schema.nullable and not schema.collection
        return self._filled(options)

    @property
    def related_model(self) -> type[Any]:
        """The model of the linked records."""
        if self.target is None:
            raise AdminSiteError(
                f"The field {self.name!r} does not know the model it links to. "
                "Put it in a view's fields, or give it target=."
            )
        return self.target

    def label_for(self, record: Any) -> str:
        """Name a single related record, using the record title if set."""
        if record is None:
            return ""
        if self.record_title is None:
            return str(record)
        return self.record_title.format_map(RecordValues(record))

    def display(self, value: Any) -> str:
        """Name the related record, or list them when there are many."""
        if value is None:
            return ""
        if self.collection or isinstance(value, list | tuple | set):
            return ", ".join(self.label_for(record) for record in value)
        return self.label_for(value)

    def parse(self, raw: str | None) -> Any:
        """Return the key that was chosen, leaving the lookup to the caller."""
        return super().parse(raw)

    def parse_many(self, raw: Iterable[str] | None) -> list[str]:
        """Return the keys chosen for a relationship holding many records."""
        keys = [value.strip() for value in raw or () if value.strip()]
        if not keys and self.required:
            raise FieldValidationError(self.name, _("This field is required."))
        return keys
