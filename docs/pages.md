# Pages and plugins

Not everything in an admin is a table. A sales report, a settings form or a button that clears a
cache are pages of your own. They live next to the model views, in the same layout, behind the same
sign in.

## A page of your own

```python
from typing import Any

from sqlalchemy import func, select
from starlette.requests import Request

from adminsite import AdminPage


class SalesReport(AdminPage):
    label = "Sales report"
    group = "Reports"
    template = "reports/sales.html"

    async def get_context(self, request: Request) -> dict[str, Any]:
        async with self.admin.database.session() as session:
            total = await session.scalar(select(func.sum(Order.total)))
        return {"total": total}


admin = Admin(
    engine, views=[OrderView], pages=[SalesReport], template_dirs=["templates"]
)
```

The page is served at `/admin/-/sales_report`, named after the class, and shows up in the sidebar
under its group and in the [command palette](views.md#the-command-palette). The template sits in
one of your `template_dirs` and extends the admin's layout:

```jinja
{% extends "adminsite/base.html" %}

{% block header %}<h1 class="text-base font-semibold">{{ page.label }}</h1>{% endblock %}

{% block content %}
  <div class="p-6">
    <div class="stat">
      <div class="stat-title">Sold so far</div>
      <div class="stat-value">{{ total }}</div>
    </div>
  </div>
{% endblock %}
```

The admin's styles are [daisyUI](https://daisyui.com) components, so a page can use the same cards,
stats and tables as the rest of the admin. `page` is the page object; `urls`, `user` and
`csrf_input` are there as on every page.

A page's `name` is its URL, `label` its text in the sidebar, `group` the sidebar section it
shares with views, and `template` what it renders; see [`AdminPage`][adminsite.AdminPage].

### Who may open it

```python
class SalesReport(AdminPage):
    async def allows(self, request: Request) -> bool:
        return request.state.user.is_manager
```

A refused page is left out of the sidebar and answers with a 403.

### Forms

A page that takes a form overrides `post`. The form's token is already checked, so a form from
another site never reaches it:

```python
from starlette.responses import RedirectResponse, Response

from adminsite.messages import add_message


class Settings(AdminPage):
    template = "settings.html"

    async def post(self, request: Request, form: dict[str, Any]) -> Response:
        await save_settings(form)
        add_message(request, "Settings saved.")
        return RedirectResponse(request.url, status_code=303)
```

Put `{{ csrf_input | safe }}` inside the `<form method="post">` in the template.

To answer with something other than a template, such as a file or JSON, override `get` and return
any Starlette response.

## Plugins

A plugin packages views, pages, routes, templates and static files so several projects can share
them. It gets the admin once and adds what it brings:

```python
from pathlib import Path

from adminsite import Plugin

HERE = Path(__file__).parent


class Reports(Plugin):
    name = "reports"

    def setup(self, admin: Admin) -> None:
        admin.add_template_dir(HERE / "templates")
        admin.add_static("reports", HERE / "static")
        admin.add_stylesheet("-/static/reports/reports.css")
        admin.add_page(SalesReport)
        admin.add_view(ReportScheduleView)
        admin.add_route("/-/reports/export.csv", export_sales)


admin = Admin(engine, plugins=[Reports()])
```

A plugin adds views with `add_view`, pages with `add_page`, any endpoint under `/-/` with
`add_route`, templates with `add_template_dir`, which a plugin can also use to replace one of the
admin's, a folder of files with `add_static`, and a stylesheet or script on every page with
`add_stylesheet` and `add_script`. `add_route(..., guarded=False)` leaves a route outside the sign
in, for a health check or a webhook. [`Admin`][adminsite.Admin] says what each takes.

The same methods work on the admin directly, without a plugin. Routes and static files have to be
added before the admin answers its first request, since the routes are fixed from then on.
