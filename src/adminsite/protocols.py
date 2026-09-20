from typing import Any, Protocol

from adminsite.schema import FieldPath, ModelSchema


class ModelInspector(Protocol):
    """Reads models of one ORM and describes them the same way for everyone."""

    def inspect(self, model: type[Any]) -> ModelSchema:
        """Describe the model, raising `NotAModelError` if it is not mapped."""
        ...

    def resolve(self, model: type[Any], path: str) -> FieldPath:
        """Resolve a dotted path such as `customer.email` against the model."""
        ...
