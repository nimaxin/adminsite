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

adminsite is not a side project. Our own business runs its back office on it every day.

adminsite is in alpha. It is tested and it works, but names may still change before 0.1.0, so pin
the version you install.

## Installation

```
pip install adminsite==0.1.0a17
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
[Getting started](https://nimaxin.github.io/adminsite/getting-started/) builds a complete app,
signing in included, in a few minutes.

## Features

- [Pages from your models](https://nimaxin.github.io/adminsite/views/): a searchable, sortable list
  with filters and saved views, a page for each record, and forms with validation.
- [Editing in the list](https://nimaxin.github.io/adminsite/views/#editing-in-the-list) for a value
  that changes often, such as an order's status.
- [Lists that keep up to date](https://nimaxin.github.io/adminsite/views/#keeping-the-list-up-to-date)
  on their own, for records that come in while people watch, such as new orders.
- [Form layouts](https://nimaxin.github.io/adminsite/views/#arranging-the-fields) with panels, rows
  and tabs, which the record page follows too.
- [Related records](https://nimaxin.github.io/adminsite/fields/#links-to-many-records): pick a
  related record from a searchable list, and edit child rows, such as an order's lines, inside the
  parent's form.
- [JSON columns](https://nimaxin.github.io/adminsite/fields/#a-form-built-from-a-schema) edited with
  a form built from a Pydantic model or a TypedDict, instead of a code box.
- [Built-in filters](https://nimaxin.github.io/adminsite/filters/) for choices, yes or no, number
  and date ranges, related records picked by name, and text.
- [Custom filters](https://nimaxin.github.io/adminsite/filters/#writing-your-own) for anything the
  built-in ones do not cover, such as "overdue" or "spent over €1,000".
- [Actions](https://nimaxin.github.io/adminsite/actions/) that run your own code on the selected
  rows, on every row the filters match, on one record or on the whole table, with a dialog for any
  values they need.
- [Permissions](https://nimaxin.github.io/adminsite/permissions/) for each view, action, field and
  row.
- [Hooks](https://nimaxin.github.io/adminsite/hooks/) that run your code before and after a save or
  delete, in the same transaction, and can refuse it with a message.
- [An audit log](https://nimaxin.github.io/adminsite/audit/): who changed what and when, including
  actions, exports and sign ins, with a history on every record and an activity page you can filter.
- [Import](https://nimaxin.github.io/adminsite/import/) from CSV or Excel with a preview of every
  row, and export to CSV.
- [File and image uploads](https://nimaxin.github.io/adminsite/fields/#files-and-pictures), stored
  on disk or wherever you choose.
- [A dashboard](https://nimaxin.github.io/adminsite/dashboard/) of stats, charts and the latest
  records.
- [A command palette](https://nimaxin.github.io/adminsite/views/#the-command-palette): Ctrl K jumps
  to any page or record.
- [A JSON API](https://nimaxin.github.io/adminsite/api/) that shares the same views and permissions.
- [Signing in](https://nimaxin.github.io/adminsite/auth/) with a fixed list of users or your own
  user table.
- [Pages and plugins](https://nimaxin.github.io/adminsite/pages/) of your own, such as reports or
  settings, in the same layout.
- [Translations](https://nimaxin.github.io/adminsite/translations/) into eleven languages, among
  them Persian and Arabic laid out right to left, and every time shown in the reader's time zone.
- [Light and dark themes](https://nimaxin.github.io/adminsite/customizing/), a layout for phones,
  and [pages that work](https://nimaxin.github.io/adminsite/accessibility/) with a keyboard and a
  screen reader.
- [Async or sync](https://nimaxin.github.io/adminsite/databases/), on SQLite, Postgres and MySQL,
  with no N+1 queries.
- [SQLModel](https://nimaxin.github.io/adminsite/databases/#sqlmodel) models as they are.
- [Large tables](https://nimaxin.github.io/adminsite/views/#large-tables): rows never wait for their
  count, which can be estimated or skipped, and page 400 costs the same as page 1.
- No Node and no CDN: everything ships inside the package.

## How it works

adminsite is one ASGI app, mounted inside yours. Every page and the JSON API go through the
`ModelView` you write for each model, so its rules hold everywhere.

![A request goes from the browser or a script to adminsite's pages or JSON API, through each model's ModelView and a SQLAlchemy repository, to your database](https://raw.githubusercontent.com/nimaxin/adminsite/main/docs/assets/architecture.png)

## Documentation

- [Getting started](https://nimaxin.github.io/adminsite/getting-started/) sets up a working admin
  in a few minutes.
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
