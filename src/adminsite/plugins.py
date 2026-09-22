from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adminsite.admin import Admin


class Plugin:
    """Something that adds to an admin, packaged so it can be shared.

    A plugin gets the admin once, in `setup`, and adds what it brings with
    the same methods a project uses:

    ```python
    class Reports(Plugin):
        name = "reports"

        def setup(self, admin):
            admin.add_template_dir(Path(__file__).parent / "templates")
            admin.add_static("reports", Path(__file__).parent / "static")
            admin.add_stylesheet("-/static/reports/reports.css")
            admin.add_page(SalesReport)
            admin.add_route("/-/reports/export", export_sales)


    admin = Admin(engine, plugins=[Reports()])
    ```
    """

    name: str = ""

    def setup(self, admin: "Admin") -> None:
        """Add the plugin's views, pages, routes, templates and assets."""
        raise NotImplementedError
