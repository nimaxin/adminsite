# Pages and plugins

Not everything in an admin is a table. A sales report, a settings form or a button that clears a
cache are pages of your own. They sit next to the model views, in the same layout, behind the same
sign in.

The examples on this page use the [example models](models.md).

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

The page is served at `/admin/-/sales_report`, named after the class, and shows in the sidebar
under its group and in the [command palette](views.md#the-command-palette). Its template sits in
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

The admin's styles are [daisyUI](https://daisyui.com) components, so a page can use the same
cards, stats and tables as the rest of the admin. The template gets `page`, and `urls`, `user` and
`csrf_input` as every page does. [`AdminPage`][adminsite.AdminPage] lists the page's settings.

### Who may open it

```python
class SalesReport(AdminPage):
    async def allows(self, request: Request) -> bool:
        return request.state.user.is_manager
```

A refused page is left out of the sidebar and answers with a 403.

### A form on the page

A page that takes a form overrides `post`. The form's token is checked first, so a form from
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

Put `{{ csrf_input | safe }}` inside the `<form method="post">` in the template. To answer with
something other than a template, such as a file or JSON, override `get` and return any Starlette
response.

## Plugins

A plugin packages views, pages, routes, templates and static files, so several projects can share
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

- `add_view` and `add_page` add views and pages.
- `add_route` adds an endpoint under `/-/`. With `guarded=False` it stays outside the sign in, for
  a health check or a webhook.
- `add_template_dir` adds templates, and can replace one of the admin's.
- `add_static` serves a folder of files, and `add_stylesheet` and `add_script` add a file to every
  page.

[`Admin`][adminsite.Admin] says what each one takes. The same methods work on the admin directly,
without a plugin. Add routes and static files before the admin answers its first request, since
its routes are fixed from then on.
