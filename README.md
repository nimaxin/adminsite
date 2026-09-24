# adminsite

An admin panel for the SQLAlchemy 2.0 ORM that works with FastAPI, Starlette and Litestar.

[![PyPI](https://img.shields.io/pypi/v/adminsite)](https://pypi.org/project/adminsite/)
[![Python](https://img.shields.io/pypi/pyversions/adminsite)](https://pypi.org/project/adminsite/)
[![CI](https://github.com/nimaxin/adminsite/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nimaxin/adminsite/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/adminsite)](https://github.com/nimaxin/adminsite/blob/main/LICENSE)

**[Live demo](https://adminsite.duckdns.org)** ·
[Documentation](https://nimaxin.github.io/adminsite/) ·
[Changelog](https://nimaxin.github.io/adminsite/changelog/) ·
[For AI assistants](https://nimaxin.github.io/adminsite/llms.txt)

[![The orders list of the demo shop](https://raw.githubusercontent.com/nimaxin/adminsite/main/docs/assets/orders.png)](https://adminsite.duckdns.org)

The demo is the example shop. Sign in as admin with the password admin and change anything you
like: it goes back as it started every hour.

adminsite is in alpha. It is tested and it works, but names may still change before 0.1.0, so pin
the version you install.

## Installation

```
pip install adminsite==0.1.0a6
```

Add the driver your database needs, such as `asyncpg` or `aiosqlite`.

## Usage

```python
from adminsite import Admin, ModelView


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total")
    search_fields = ("customer.name", "customer.email")
    list_filter = ("status", "created_at")


app.mount("/admin", Admin(engine, views=[OrderView]))
```

The labels, filters, form controls and validation come from your models.
[Getting started](https://nimaxin.github.io/adminsite/getting-started/) builds a complete app, signing in included, in a few
minutes.

## Features

- **[Pages from your models](https://nimaxin.github.io/adminsite/views/):** a searchable, sortable list with filters and saved
  views, a page for each record, and forms with validation.
- **[Related records](https://nimaxin.github.io/adminsite/fields/#links-to-many-records):** pick a related record from a
  searchable list, and edit child rows, such as an order's lines, inside the parent's form.
- **[Built-in filters](https://nimaxin.github.io/adminsite/filters/)** for choices, yes or no, number and date ranges, related
  records and text.
- **[Custom filters](https://nimaxin.github.io/adminsite/filters/#writing-your-own)** for anything the built-in ones do not
  cover, such as "overdue" or "spent over €1,000".
- **[Actions](https://nimaxin.github.io/adminsite/actions/)** that run your own code on the selected rows, on every row the
  filters match, on one record or on the whole table, with a dialog for any values they need.
- **[Permissions](https://nimaxin.github.io/adminsite/permissions/)** for each view, action, field and row.
- **[Hooks](https://nimaxin.github.io/adminsite/hooks/)** that run your code before and after a save or delete, in the same
  transaction, and can refuse it with a message.
- **[An audit log](https://nimaxin.github.io/adminsite/audit/):** who changed what and when, including actions, exports and sign
  ins, with a history on every record and an activity page you can filter.
- **[Import](https://nimaxin.github.io/adminsite/import/)** from CSV or Excel with a preview of every row, and export to CSV.
- **[File and image uploads](https://nimaxin.github.io/adminsite/fields/#files-and-pictures)**, stored on disk or wherever you
  choose.
- **[A dashboard](https://nimaxin.github.io/adminsite/dashboard/)** of stats, charts and the latest records.
- **[A JSON API](https://nimaxin.github.io/adminsite/api/)** that shares the same views and permissions.
- **[Signing in](https://nimaxin.github.io/adminsite/auth/)** with a fixed list of users or your own user table.
- **[Pages and plugins](https://nimaxin.github.io/adminsite/pages/)** of your own, such as reports or settings, in the same
  layout.
- **[Translations](https://nimaxin.github.io/adminsite/translations/)**, with Persian built in and right to left layouts.
- **[Light and dark themes](https://nimaxin.github.io/adminsite/customizing/)**, a layout for phones, and
  [pages that work](https://nimaxin.github.io/adminsite/accessibility/) with a keyboard and a screen reader.
- **[Async or sync](https://nimaxin.github.io/adminsite/databases/)**, on SQLite, Postgres and MySQL, with no N+1 queries.
- **No Node and no CDN:** everything ships inside the package.

<p align="center">
  <img src="https://raw.githubusercontent.com/nimaxin/adminsite/main/docs/assets/record.png" alt="An order's page, with its history" width="40%">
  <img src="https://raw.githubusercontent.com/nimaxin/adminsite/main/docs/assets/overview-dark.png" alt="The overview in dark mode" width="40%">
  <img src="https://raw.githubusercontent.com/nimaxin/adminsite/main/docs/assets/phone.png" alt="The orders list on a phone" width="15.7%">
</p>

## Documentation

- [Getting started](https://nimaxin.github.io/adminsite/getting-started/) sets up a working admin
  in a few minutes.
- [Views](https://nimaxin.github.io/adminsite/views/) covers everything a view can say about a
  model.
- [Permissions](https://nimaxin.github.io/adminsite/permissions/) is worth reading before you put
  the admin in front of anyone.
- The [reference](https://nimaxin.github.io/adminsite/reference/) lists every class and option.

## Contributing

Issues and pull requests are welcome.
[CONTRIBUTING.md](https://github.com/nimaxin/adminsite/blob/main/CONTRIBUTING.md) says how to run
the tests and rebuild the stylesheet.

## License

[MIT](https://github.com/nimaxin/adminsite/blob/main/LICENSE)
