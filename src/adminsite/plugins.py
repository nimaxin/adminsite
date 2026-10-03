from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "Plugin",
]


class Plugin:
    """Something that adds to an admin, packaged so it can be shared.

    A plugin gets the admin once, in `setup`, and adds what it brings with
    the same methods a project uses:

    ```python
    from pathlib import Path

    HERE = Path(__file__).parent


    class Reports(Plugin):
        name = "reports"

        def setup(self, admin: Admin) -> None:
            admin.add_template_dir(HERE / "templates")
            admin.add_static("reports", HERE / "static")
            admin.add_stylesheet("-/static/reports/reports.css")
            admin.add_page(SalesReport)
            admin.add_route("/-/reports/export", export_sales)


    admin = Admin(engine, plugins=[Reports()])
    ```
    """

    name: str = ""
    """A short name for the plugin, such as "reports"."""

    def setup(self, admin: "Admin") -> None:
        """Add the plugin's views, pages, routes, templates and assets.

        Args:
            admin: The admin being built, whose `add_` methods the plugin
                calls.
        """
        raise NotImplementedError
