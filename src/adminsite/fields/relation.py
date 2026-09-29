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
    records. `view` names the view its links open and its picker lists
    from, for a model shown by more than one view; left out, the first view
    of that model.
    """

    _: KW_ONLY
    # The model of the linked records. Left out, the relationship's.
    target: type[Any] | None = None
    collection: bool = False
    display_template: str | None = None
    view: "type[ModelView[Any]] | str | None" = None
    # For a link to many whose order means something, such as servers tried
    # in turn: the form can put its records in order, and saving writes the
    # link again in that order when it changed.
    ordered: bool = False

    widget = "relation"
    python_type = str
    error_message = "Choose a record."

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
        """Name a single related record, using the display template if set."""
        if record is None:
            return ""
        if self.display_template is None:
            return str(record)
        return self.display_template.format_map(RecordValues(record))

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
