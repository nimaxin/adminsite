# adminsite

An admin panel for the SQLAlchemy 2.0 ORM that works with FastAPI, Starlette and Litestar.

[![PyPI](https://img.shields.io/pypi/v/adminsite)](https://pypi.org/project/adminsite/)
[![Python](https://img.shields.io/pypi/pyversions/adminsite)](https://pypi.org/project/adminsite/)
[![CI](https://github.com/nimaxin/adminsite/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nimaxin/adminsite/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/adminsite)](https://github.com/nimaxin/adminsite/blob/main/LICENSE)

**[Live demo](https://adminsite.duckdns.org)** ·
[Documentation](https://nimaxin.github.io/adminsite/) ·
[Changelog](https://nimaxin.github.io/adminsite/changelog/) ·
[Discussions](https://github.com/nimaxin/adminsite/discussions) ·
[For AI assistants](https://nimaxin.github.io/adminsite/llms.txt)

[![The orders list of the demo shop](https://raw.githubusercontent.com/nimaxin/adminsite/main/docs/assets/orders.png)](https://adminsite.duckdns.org)

## Installation

```
pip install adminsite
```

Add the driver your database needs, such as `asyncpg` or `aiosqlite`.

## Usage

```python
from adminsite import Admin, Link, ModelView


class OrderView(ModelView[Order]):
    fields = [Order.id, Order.customer, Order.status, Order.total]
    searchable_fields = [
        Link(Order.customer, Customer.name),
        Link(Order.customer, Customer.email),
    ]
    list_filters = [Order.status, Order.created_at]


app.mount("/admin", Admin(engine, views=[OrderView]))
```

The settings name your models' own attributes, so a type checker catches a misspelt one. The labels,
filters, form controls and validation come from your models.
[Getting started](https://nimaxin.github.io/adminsite/getting-started/) builds a working admin in
one file.

## Features

- [Lists](https://nimaxin.github.io/adminsite/views/#the-list) with search, sorting, saved views,
  editing in the list and auto refresh.
- [Filters](https://nimaxin.github.io/adminsite/filters/) for text, numbers, dates, choices and
  related records, plus
  [custom filters](https://nimaxin.github.io/adminsite/filters/#write-your-own).
- [Forms and record pages](https://nimaxin.github.io/adminsite/views/#forms) laid out in panels,
  rows and tabs.
- [Related records](https://nimaxin.github.io/adminsite/fields/#relationships) picked from a
  searchable list, and
  [child rows](https://nimaxin.github.io/adminsite/views/#edit-related-records-in-the-same-form)
  edited inside the parent's form.
- [JSON columns](https://nimaxin.github.io/adminsite/fields/#a-form-from-a-schema) edited as a
  form built from a Pydantic model or a TypedDict.
- [File and image uploads](https://nimaxin.github.io/adminsite/fields/#files-and-pictures), stored
  on disk or wherever you choose.
- [Custom actions](https://nimaxin.github.io/adminsite/actions/) on the selected rows, every row the
  filters match, one record or the whole table.
- [Hooks](https://nimaxin.github.io/adminsite/hooks/) that run in the same transaction as a save or
  delete, and can refuse it.
- [Permissions](https://nimaxin.github.io/adminsite/permissions/) for each model, action, field and
  row.
- [Built-in sign in](https://nimaxin.github.io/adminsite/auth/), with users from your own user
  table.
- [Users and roles](https://nimaxin.github.io/adminsite/auth/#users-and-roles-in-the-admin) managed
  in the admin, each role a grid of what it may do.
- [Audit log](https://nimaxin.github.io/adminsite/audit/) of who changed what and when, with a
  history on every record.
- [Import](https://nimaxin.github.io/adminsite/import/) from CSV or Excel with a preview, and
  export to CSV.
- [JSON API](https://nimaxin.github.io/adminsite/api/) with the same permissions as the pages.
- [Dashboard](https://nimaxin.github.io/adminsite/dashboard/),
  [Ctrl K search](https://nimaxin.github.io/adminsite/views/#the-command-palette) and
  [custom pages](https://nimaxin.github.io/adminsite/pages/).
- [Light and dark themes](https://nimaxin.github.io/adminsite/customizing/#colours-and-theme),
  [compact mode](https://nimaxin.github.io/adminsite/customizing/#compact-mode) and a mobile layout.
- [Keyboard and screen reader support](https://nimaxin.github.io/adminsite/accessibility/).
- [Translations](https://nimaxin.github.io/adminsite/translations/) into eleven languages, some
  right to left, and times in each reader's time zone.
- [Async or sync engines](https://nimaxin.github.io/adminsite/databases/#async-or-sync) on SQLite,
  Postgres and MySQL, with no N+1 queries.
- [SQLModel](https://nimaxin.github.io/adminsite/databases/#sqlmodel) models work too.
- [Fast on large tables](https://nimaxin.github.io/adminsite/views/#large-tables): counts can be
  estimated or skipped, and page 400 loads as fast as page 1.
- No Node and no CDN: everything ships inside the package.

## Motivation

Our business ran its back office on [sqladmin](https://github.com/smithyhq/sqladmin), and then on
[starlette-admin](https://github.com/jowilf/starlette-admin). We needed features neither had, and in
the end I had changed starlette-admin so much that turning it into pull requests would have taken
more time than I had. So I built adminsite on what we learned from both, with everything our team
needs.

It is not a side project. Our back office runs on it every day, and I keep developing it. An admin
panel is not what sets our business apart, so it is open source, for anyone to use and improve.

adminsite is for SQLAlchemy only, and will stay that way. SQLModel models work too, since they are
SQLAlchemy models, but I do not use SQLModel myself. If you do, reports and fixes are welcome.

## Documentation

- [Getting started](https://nimaxin.github.io/adminsite/getting-started/) builds a working admin
  in one file.
- [Views](https://nimaxin.github.io/adminsite/views/) covers everything a view can say about a
  model.
- [Permissions](https://nimaxin.github.io/adminsite/permissions/) is worth reading before you put
  the admin in front of anyone.
- The [reference](https://nimaxin.github.io/adminsite/reference/) lists every class and option.

## Contributing

Ask a question or share an idea in
[Discussions](https://github.com/nimaxin/adminsite/discussions). Issues and pull requests are
welcome. [CONTRIBUTING.md](https://github.com/nimaxin/adminsite/blob/main/CONTRIBUTING.md) says
how to run the tests and rebuild the stylesheet.

## License

[MIT](https://github.com/nimaxin/adminsite/blob/main/LICENSE)
