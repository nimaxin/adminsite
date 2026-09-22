# Pages and plugins

Not everything in an admin is a table. A sales report, a settings form or a button that clears a
cache are pages of your own. They live next to the model views, in the same layout, behind the same
sign in.

## A page of your own

```python
from adminsite import AdminPage
from sqlalchemy import func, select


class SalesReport(AdminPage):
    label = "Sales report"
    group = "Reports"
    template = "reports/sales.html"

    async def get_context(self, request):
        async with self.admin.database.session() as session:
            total = await session.scalar(select(func.sum(Order.total)))
        return {"total": total}


admin = Admin(engine, views=[OrderView], pages=[SalesReport], template_dirs=["templates"])
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

| Setting | What it does |
|---|---|
| `name` | The URL, `/-/` and the name. Defaults to the class name in snake case, without `Page`. |
| `label` | The sidebar text. Defaults to the name, spaced out. |
| `group` | The sidebar section. Pages share groups with views. |
| `template` | The template to render. |

### Who may open it

```python
class SalesReport(AdminPage):
    async def allows(self, request):
        return request.state.user.is_manager
```

A refused page is left out of the sidebar and answers with a 403.

### Forms

A page that takes a form overrides `post`. The form's token is already checked, so a form from
another site never reaches it:

```python
from starlette.responses import RedirectResponse

from adminsite.http.templating import add_message


class Settings(AdminPage):
    template = "settings.html"

    async def post(self, request, form):
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

    def setup(self, admin):
        admin.add_template_dir(HERE / "templates")
        admin.add_static("reports", HERE / "static")
        admin.add_stylesheet("-/static/reports/reports.css")
        admin.add_page(SalesReport)
        admin.add_view(ReportScheduleView)
        admin.add_route("/-/reports/export.csv", export_sales)


admin = Admin(engine, plugins=[Reports()])
```

| Method | What it adds |
|---|---|
| `add_view(view)` | A model view. |
| `add_page(page)` | A page of your own. |
| `add_route(path, endpoint, methods=("GET",), guarded=True)` | Any endpoint, called as `endpoint(admin, request)`. The path starts with `/-/`, so it never meets a model's URL. `guarded=False` leaves it outside the sign in, for a health check or a webhook. |
| `add_template_dir(path)` | A folder of templates, searched before the admin's own, so a plugin can also replace one of them. |
| `add_static(name, path)` | A folder served at `/-/static/` and the name. |
| `add_stylesheet(href)`, `add_script(src)` | A stylesheet or script on every page. A relative path starts at the admin, a full URL is used as it is. |

The same methods work on the admin directly, without a plugin. Routes and static files have to be
added before the admin answers its first request, since the routes are fixed from then on.
