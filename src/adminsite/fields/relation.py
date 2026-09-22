from collections.abc import Iterable
from typing import Any

from adminsite.fields.base import Field
from adminsite.schema import RelationSchema
from adminsite.text import RecordValues


class RelationField(Field):
    """A link to one or many other records."""

    widget = "relation"
    python_type = str
    error_message = "Choose a record."

    def __init__(
        self,
        name: str,
        *,
        target: type[Any],
        collection: bool = False,
        display_template: str | None = None,
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.target = target
        self.collection = collection
        self.display_template = display_template

    @classmethod
    def from_relation(cls, schema: RelationSchema, **overrides: Any) -> "RelationField":
        """Build the field from an inspected relationship."""
        options: dict[str, Any] = {
            "label": schema.label,
            "target": schema.target,
            "collection": schema.collection,
            "required": not schema.nullable and not schema.collection,
        }
        options.update(overrides)
        return cls(schema.name, **options)

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
        if raw is None:
            return []
        return [value.strip() for value in raw if value.strip()]
