# Working on adminsite

adminsite is an admin panel for SQLAlchemy 2.0 models, served as an ASGI app that people mount
into Starlette, FastAPI or Litestar. Python 3.11+, managed with uv.

This file is for agents changing adminsite itself. Agents using adminsite in another project should
read https://nimaxin.github.io/adminsite/llms.txt instead.

## Layout

- `src/adminsite/` is the package. `admin.py` builds the app and its routes, `views/` holds
  `ModelView` and the parts it is built from (below), `_http/` holds one module per page,
  `_orm/` builds the statements a view runs, `database.py` hands out sessions, `inspector.py`
  reads the models, and `fields/` and `filters/` are what they say. A name starting with an
  underscore is machinery no project imports.
- `src/adminsite/templates/adminsite/` holds the Jinja templates; `widgets/` has one file per form
  control.
- `src/adminsite/static/adminsite.css` is built from `frontend/` and committed. HTMX and Alpine are
  vendored next to it.
- `src/adminsite/locales/` holds the translation catalogs, one JSON file per language.
- `docs/` is the MkDocs site; `examples/shop.py` is the demo shop, and `examples/fields.py` a
  gallery with every kind of field, each labelled by its class. Add a new field type to it.
- `demo/` serves both at https://adminsite.duckdns.org, signed in as admin / admin and reset
  every hour. `.github/workflows/demo.yml` deploys it after CI passes on main; `demo/README.md`
  describes the server.
- `tests/` runs every database test on SQLite async and sync, and on Postgres and MySQL when their
  URLs are set.

## Inside views/

`ModelView` keeps what users write: the settings, the `get_` methods, `allows`, `scope_query`,
`get_actions` and the hooks. The work is done by parts it builds in `__init__`, from the bottom row
up, each handed the parts it uses. Templates and `_http/` reach a part through the view, such as
`view._pages.form_fields(request, record)`. A part's module is named for the attribute the view
holds it as, so `view._reader` lives in `_reader.py`, and the modules a project imports carry no
underscore. A module imports only modules of the rows below its own; `tests/test_view_layers.py`
fails otherwise.

| Row | Module | What it does |
| --- | --- | --- |
| 1 | `_checks.py` | The startup checks, and the messages they stop the admin with |
| 1 | `_linked_names.py` | How a linked record is named |
| 1 | `layout.py` | `PanelWidget`, `FieldsetWidget`, `RowWidget`, `TabsWidget`, and arranging them for one page |
| 1 | `inlines.py` | `Inline`, the setting |
| 1 | `contexts.py` | `SaveContext`, `DeleteContext` and `SaveValues`, which the hooks get |
| 2 | `_settings.py` | The settings read into paths and checked, once, at startup |
| 3 | `_fields.py` | The field for each path, and what a record holds and shows there |
| 3 | `_inline_views.py` | The child view of each inline |
| 4 | `_pages.py` | The fields each page shows this user, and which of them are editable |
| 5 | `_reader.py` | Pages of records, one record, filter choices and computed values |
| 5 | `_forms.py` | A submitted form, its inline rows and an action's inputs, as values |
| 5 | `_audit.py` | The entries a change writes to the audit log |
| 6 | `_links.py` | The records a submitted link names, read through the linked view |
| 7 | `_saver.py` | A save or a delete in one transaction, with its hooks and files |
| 7 | `_actions.py` | The actions offered to a request, run and written down |
| 8 | `model_view.py` | `ModelView` itself |
| 9 | `registry.py`, `_picker.py` | The admin's views; the records a link may offer |

Where a change goes:

- **A setting that names columns:** an attribute on `ModelView`, with a docstring under it, read
  into paths in `SettingsReader.__init__`, which checks every name. When the answer can depend on
  the request, add a `get_` method beside it; `PageFields` calls it and checks an answer other
  than the setting itself through `SettingsReader`.
- **A startup check:** a function in `_checks.py`, called where the setting is read.
- **A hook:** a method on `ModelView` that does nothing by default, called by the part that does
  the work, such as `Saver` for a save.
- **A rule about which fields a page shows:** `PageFields`, which the pages, the export, the
  import and the API all ask.
- **Where a page puts its fields:** `form_layout`, read in `SettingsReader` and arranged for each
  page by `PageFields.arranged`; the form and the record page draw what it returns.
- **A new module:** a row in `LAYERS` in `tests/test_view_layers.py`, and in the table above.

## Commands

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run python -m tests.doc_examples
uv run pytest -q
uv run --group docs mkdocs build --strict
```

Postgres and MySQL:

```bash
docker run -d --name adminsite-postgres -p 55432:5432 -e POSTGRES_USER=adminsite -e POSTGRES_PASSWORD=adminsite -e POSTGRES_DB=adminsite postgres:17-alpine
docker run -d --name adminsite-mysql -p 53306:3306 -e MYSQL_ROOT_PASSWORD=adminsite -e MYSQL_DATABASE=adminsite -e MYSQL_USER=adminsite -e MYSQL_PASSWORD=adminsite mysql:8.4
ADMINSITE_POSTGRES_URL=postgresql://adminsite:adminsite@localhost:55432/adminsite \
ADMINSITE_MYSQL_URL=mysql://adminsite:adminsite@127.0.0.1:53306/adminsite \
uv run pytest -q
```

After changing classes in a template, rebuild the stylesheet, or the new classes will not exist:

```bash
cd frontend && npx @tailwindcss/cli -i ./input.css -o ../src/adminsite/static/adminsite.css --minify
```

Run the demo with `uv run uvicorn examples.shop:app --reload`, then open
http://127.0.0.1:8000/admin and sign in as nima / letmein. To see every field after changing one,
run `uv run uvicorn examples.fields:app --reload` instead; it needs no sign in.

## Rules that are easy to break

- **Every visible text is translated.** Templates use `{{ _('Save') }}`, Python uses
  `from adminsite.i18n import gettext as _` and `_("{thing} saved.", thing=...)` with named
  placeholders, never f-strings. Add each new text to `locales/fa.json`;
  `uv run python -m tests.messages fa` lists what is missing, and a test fails until it is done.
- **Layouts mirror for right to left languages.** Use `ms-`, `me-`, `ps-`, `pe-`, `start-`, `end-`,
  `text-start`, `text-end`, `border-s`, `border-e`, never left or right.
- **Every control has an accessible name**: a `<label for>`, `aria-label` or `aria-labelledby`.
  `tests/test_accessibility.py` checks the main pages.
- **Reads go through the view**, so `scope_query` and `allows` always apply. Never query a model
  directly from an endpoint. Nothing under `_http/` imports SQLAlchemy or the ORM layer
  (`adminsite._orm`, `adminsite.database`, `adminsite.inspector`, the SQL filters) when it runs,
  only `SessionAdapter` under `TYPE_CHECKING` for annotations; `tests/test_web_layer.py` fails
  otherwise. Catch `adminsite.exceptions.IntegrityError`, which the session raises for a change
  the database refuses, never SQLAlchemy's.
- **No N+1 queries.** Load what a page shows with the page; the tests count statements.
- **Code must work on SQLite, Postgres and MySQL.** Convert keys read from URLs with
  `to_column_type`. MySQL refuses a `LIMIT` straight inside `IN (...)`.
- **The session is only saved when a key is set.** Assign a new value; never change a list or
  dict inside it in place.
- **Examples type-check.** `python -m tests.doc_examples` runs mypy strict over every python block
  in `docs/`, the README and the docstrings, and refuses a view setting `ModelView` does not have.
  A name an example takes as given, such as a model, goes in `tests/doc_examples/context.py`.
- **Text files use LF endings.** `.gitattributes` enforces it; keep it that way when writing files
  from scripts.

## Style

- Write like a person: plain, specific, short. No marketing tone, no filler, no "seamless" or
  "robust". Avoid em dashes; use a full stop, a comma or a colon.
- Names read as words: `fetch_page`, `can_import`, `saved_for`. No abbreviations.
- Docstrings say what a thing is or does in one line. Comments explain why, and only where the code
  cannot.
- What `docs/reference.md` shows is documented in full, in Google style, because the Reference
  page and editors read it from there: `Args:` for each parameter, the constructor's in the class
  docstring; `Returns:` for what it answers; `Raises:` for an error the caller is meant to handle;
  and a docstring under each attribute people set or read, such as a setting of `ModelView`.
  Options a class inherits are described once, on the class they come from.
  `tests/test_reference_docs.py` fails on a gap. Everything else keeps its one line, plus a
  sentence for a parameter whose name and type do not say enough.
- Messages tell people what happened and what to do: "Keep the file under 5 MB.", not
  "Error: file size exceeded".
- ruff at 88 columns and mypy strict are the rules; do not silence them without a reason in a
  comment.

## Workflow

- Plan first when asked to plan, and wait for approval before building.
- Commit after each finished step, with a message such as `feat: ...`, `fix: ...` or `docs: ...`.
- Add a line to `CHANGELOG.md` under Unreleased for anything a user would notice, and update the
  docs page the change belongs to.
- Never push, tag or publish unless asked. A tag `v*` publishes to PyPI.
