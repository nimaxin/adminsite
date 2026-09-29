from importlib.metadata import version
from typing import TYPE_CHECKING

from adminsite.admin import Admin
from adminsite.backends.sqlalchemy.repository import Statement
from adminsite.columns import ColumnReference, Descending, Link
from adminsite.dashboard import Chart, ModelCounts, RecentRecords, Stat, Widget
from adminsite.exceptions import (
    AdminSiteError,
    InvalidPathError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
    UnknownFieldError,
    renamed_names,
)
from adminsite.fields import BaseField, Field, FieldOptions
from adminsite.messages import Message
from adminsite.pages import AdminPage
from adminsite.plugins import Plugin
from adminsite.protocols import ModelInspector
from adminsite.query import CountMode, Page, Pagination, QuerySpec, Sort
from adminsite.saved_views import SavedView, SavedViews
from adminsite.schema import (
    FieldPath,
    FieldSchema,
    ModelSchema,
    RelationDirection,
    RelationSchema,
)
from adminsite.security import Permission, RequestAction
from adminsite.text import Html
from adminsite.views import (
    DeleteContext,
    Inline,
    ModelView,
    SaveContext,
    ViewRegistry,
)

__version__ = version("adminsite")

__all__ = [
    "Admin",
    "AdminPage",
    "AdminSiteError",
    "BaseField",
    "Chart",
    "ColumnReference",
    "CountMode",
    "DeleteContext",
    "Descending",
    "Field",
    "FieldOptions",
    "FieldPath",
    "FieldSchema",
    "Html",
    "Inline",
    "InvalidPathError",
    "Link",
    "Message",
    "ModelCounts",
    "ModelInspector",
    "ModelSchema",
    "ModelView",
    "NotAModelError",
    "Page",
    "Pagination",
    "Permission",
    "PermissionDeniedError",
    "Plugin",
    "QuerySpec",
    "RecentRecords",
    "RecordNotFoundError",
    "RefusedError",
    "RelationDirection",
    "RelationSchema",
    "RequestAction",
    "SaveContext",
    "SavedView",
    "SavedViews",
    "Sort",
    "Stat",
    "Statement",
    "UnknownFieldError",
    "ViewRegistry",
    "Widget",
    "__version__",
]


if not TYPE_CHECKING:
    # The names 0.1.0a10 changed, refused with the name each has now. Hidden
    # from type checkers, which report an old name as missing.
    __getattr__ = renamed_names(__name__, {"Computed": "ComputedField"})
