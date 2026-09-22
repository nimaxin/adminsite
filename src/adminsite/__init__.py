from importlib.metadata import version

from adminsite.admin import Admin
from adminsite.exceptions import (
    AdminSiteError,
    InvalidPathError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
    UnknownFieldError,
)
from adminsite.protocols import ModelInspector
from adminsite.query import CountMode, Page, Pagination, QuerySpec, Sort
from adminsite.schema import (
    FieldPath,
    FieldSchema,
    ModelSchema,
    RelationDirection,
    RelationSchema,
)
from adminsite.security import Permission
from adminsite.views import Inline, ModelView, ViewRegistry

__version__ = version("adminsite")

__all__ = [
    "Admin",
    "AdminSiteError",
    "CountMode",
    "FieldPath",
    "FieldSchema",
    "Inline",
    "InvalidPathError",
    "ModelInspector",
    "ModelSchema",
    "ModelView",
    "NotAModelError",
    "Page",
    "Pagination",
    "Permission",
    "PermissionDeniedError",
    "QuerySpec",
    "RecordNotFoundError",
    "RefusedError",
    "RelationDirection",
    "RelationSchema",
    "Sort",
    "UnknownFieldError",
    "ViewRegistry",
    "__version__",
]
