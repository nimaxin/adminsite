# Working on adminsite

adminsite is an admin panel for SQLAlchemy 2.0 models, served as an ASGI app that people mount
into Starlette, FastAPI or Litestar. Python 3.11+, managed with uv.

This file is for agents changing adminsite itself. Agents using adminsite in another project should
read https://nimaxin.github.io/adminsite/llms.txt instead.

## Layout

- `src/adminsite/` is the package. `admin.py` builds the app and its routes, `views/` holds
  `ModelView`, `http/` holds the endpoints, `backends/sqlalchemy/` holds everything that touches
  SQLAlchemy, `fields/` and `filters/` are what they say.
- `src/adminsite/templates/adminsite/` holds the Jinja templates; `widgets/` has one file per form
  control.
- `src/adminsite/static/adminsite.css` is built from `frontend/` and committed. HTMX and Alpine are
  vendored next to it.
- `src/adminsite/locales/` holds the translation catalogs, one JSON file per language.
- `docs/` is the MkDocs site; `examples/shop.py` is the demo.
- `tests/` runs every database test on SQLite async and sync, and on Postgres and MySQL when their
  URLs are set.

## Commands

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
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
http://127.0.0.1:8000/admin and sign in as nima / letmein.

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
  directly from an endpoint.
- **No N+1 queries.** Load what a page shows with the page; the tests count statements.
- **Code must work on SQLite, Postgres and MySQL.** Convert keys read from URLs with
  `to_column_type`. MySQL refuses a `LIMIT` straight inside `IN (...)`.
- **The session is only saved when a key is set.** Assign a new value; never change a list or
  dict inside it in place.
- **Text files use LF endings.** `.gitattributes` enforces it; keep it that way when writing files
  from scripts.

## Style

- Write like a person: plain, specific, short. No marketing tone, no filler, no "seamless" or
  "robust". Avoid em dashes; use a full stop, a comma or a colon.
- Names read as words: `fetch_page`, `can_import`, `saved_for`. No abbreviations.
- Docstrings say what a thing is or does in one line. Comments explain why, and only where the code
  cannot.
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
