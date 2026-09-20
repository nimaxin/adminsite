from importlib.metadata import version

from adminsite.exceptions import (
    AdminSiteError,
    InvalidPathError,
    NotAModelError,
    UnknownFieldError,
)
from adminsite.protocols import ModelInspector
from adminsite.schema import (
    FieldPath,
    FieldSchema,
    ModelSchema,
    RelationDirection,
    RelationSchema,
)

__version__ = version("adminsite")

__all__ = [
    "AdminSiteError",
    "FieldPath",
    "FieldSchema",
    "InvalidPathError",
    "ModelInspector",
    "ModelSchema",
    "NotAModelError",
    "RelationDirection",
    "RelationSchema",
    "UnknownFieldError",
    "__version__",
]
