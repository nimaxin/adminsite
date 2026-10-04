from importlib.metadata import version

from adminsite.admin import Admin
from adminsite.columns import ColumnReference, Descending, Link
from adminsite.dashboard import Chart, ModelCounts, RecentRecords, Stat, Widget
from adminsite.database import Statement
from adminsite.exceptions import (
    AdminSiteError,
    InvalidPathError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
    UnknownFieldError,
)
from adminsite.fields import BaseField, Field
from adminsite.messages import Message
from adminsite.pages import AdminPage
from adminsite.plugins import Plugin
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
    FieldsetWidget,
    Inline,
    ModelView,
    PanelWidget,
    RowWidget,
    SaveContext,
    TabsWidget,
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
    "FieldPath",
    "FieldSchema",
    "FieldsetWidget",
    "Html",
    "Inline",
    "InvalidPathError",
    "Link",
    "Message",
    "ModelCounts",
    "ModelSchema",
    "ModelView",
    "NotAModelError",
    "Page",
    "Pagination",
    "PanelWidget",
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
    "RowWidget",
    "SaveContext",
    "SavedView",
    "SavedViews",
    "Sort",
    "Stat",
    "Statement",
    "TabsWidget",
    "UnknownFieldError",
    "ViewRegistry",
    "Widget",
    "__version__",
]
