from importlib.metadata import version

from adminsite.admin import Admin
from adminsite.exceptions import (
    AdminSiteError,
    InvalidPathError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    UnknownFieldError,
)
from adminsite.protocols import ModelInspector
from adminsite.query import CountMode, Page, QuerySpec, Sort
from adminsite.schema import (
    FieldPath,
    FieldSchema,
    ModelSchema,
    RelationDirection,
    RelationSchema,
)
from adminsite.security import Action
from adminsite.views import ModelView, ViewRegistry

__version__ = version("adminsite")

__all__ = [
    "Action",
    "Admin",
    "AdminSiteError",
    "CountMode",
    "FieldPath",
    "FieldSchema",
    "InvalidPathError",
    "ModelInspector",
    "ModelSchema",
    "ModelView",
    "NotAModelError",
    "Page",
    "PermissionDeniedError",
    "QuerySpec",
    "RecordNotFoundError",
    "RelationDirection",
    "RelationSchema",
    "Sort",
    "UnknownFieldError",
    "ViewRegistry",
    "__version__",
]
