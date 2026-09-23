from importlib.metadata import version

from adminsite.admin import Admin
from adminsite.dashboard import Chart, ModelCounts, RecentRecords, Stat, Widget
from adminsite.exceptions import (
    AdminSiteError,
    InvalidPathError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
    UnknownFieldError,
)
from adminsite.fields import Computed, FieldOptions
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
from adminsite.security import Permission
from adminsite.text import Html
from adminsite.views import Inline, ModelView, ViewRegistry

__version__ = version("adminsite")

__all__ = [
    "Admin",
    "AdminPage",
    "AdminSiteError",
    "Chart",
    "Computed",
    "CountMode",
    "FieldOptions",
    "FieldPath",
    "FieldSchema",
    "Html",
    "Inline",
    "InvalidPathError",
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
    "SavedView",
    "SavedViews",
    "Sort",
    "Stat",
    "UnknownFieldError",
    "ViewRegistry",
    "Widget",
    "__version__",
]
