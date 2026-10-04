"""JSON documents edited as forms, drawn from a schema.

A schema is a Pydantic type, such as a model or a TypedDict, or a JSON Schema
written as a dict. `Document` reads it into shapes:

- a single value, drawn and read by one of the ordinary fields;
- a group of properties, each under its title;
- rows of objects, added and removed like the rows of a table;
- pairs, a map from keys to values, its keys chosen from a fixed set where
  the schema has one;
- a fixed value, which the form never asks for.

Whatever no shape covers, such as a choice between objects of different
shapes, is edited as JSON in a code box.

Every part of a document has an input name of its own under the field's:
`settings.free_shipping_over`, `settings.channels.0.id`.
"""

__all__ = []
