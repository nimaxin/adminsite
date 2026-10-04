from importlib.metadata import version

from adminsite.admin import Admin
from adminsite.columns import ColumnReference, Descending, Link
from adminsite.dashboard import Chart, ModelCounts, RecentRecords, Stat, Widget
from adminsite.database import Statement
from adminsite.exceptions import (
    AdminSiteError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
)
from adminsite.fields import BaseField, Field
from adminsite.markup import Html
from adminsite.messages import Message
from adminsite.pages import AdminPage
from adminsite.permissions import Permission, RequestAction
from adminsite.plugins import Plugin
from adminsite.query import CountMode, Pagination
from adminsite.saved_views import SavedViews
from adminsite.views import (
    DeleteContext,
    FieldsetWidget,
    Inline,
    ModelView,
    PanelWidget,
    RowWidget,
    SaveContext,
    TabsWidget,
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
    "FieldsetWidget",
    "Html",
    "Inline",
    "Link",
    "Message",
    "ModelCounts",
    "ModelView",
    "Pagination",
    "PanelWidget",
    "Permission",
    "PermissionDeniedError",
    "Plugin",
    "RecentRecords",
    "RecordNotFoundError",
    "RefusedError",
    "RequestAction",
    "RowWidget",
    "SaveContext",
    "SavedViews",
    "Stat",
    "Statement",
    "TabsWidget",
    "Widget",
    "__version__",
]
