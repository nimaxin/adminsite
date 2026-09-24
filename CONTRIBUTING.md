# Contributing to adminsite

Issues and pull requests are both welcome. For anything bigger than a fix, open an issue first, so
we can agree on the shape of it before you write it.

## Setting up

adminsite needs Python 3.11 or newer and is managed with [uv](https://docs.astral.sh/uv/):

```
uv sync --all-groups
```

## Trying it

```
uv run uvicorn examples.shop:app --reload
```

Then open http://127.0.0.1:8000/admin and sign in as nima / letmein. The demo that runs online is
the same shop, served by `demo/app.py`; [demo/README.md](demo/README.md) describes it.

## Checking a change

```
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest -q
uv run --group docs mkdocs build --strict
```

The tests run every database test on SQLite, async and sync. To run them on Postgres and MySQL
too, start both and point the tests at them:

```
docker run -d --name adminsite-postgres -p 55432:5432 -e POSTGRES_USER=adminsite -e POSTGRES_PASSWORD=adminsite -e POSTGRES_DB=adminsite postgres:17-alpine
docker run -d --name adminsite-mysql -p 53306:3306 -e MYSQL_ROOT_PASSWORD=adminsite -e MYSQL_DATABASE=adminsite -e MYSQL_USER=adminsite -e MYSQL_PASSWORD=adminsite mysql:8.4
ADMINSITE_POSTGRES_URL=postgresql://adminsite:adminsite@localhost:55432/adminsite \
ADMINSITE_MYSQL_URL=mysql://adminsite:adminsite@127.0.0.1:53306/adminsite \
uv run pytest -q
```

CI runs all of this on every push and pull request.

## The stylesheet

The stylesheet, HTMX, Alpine and the Geist typeface are built or copied from `frontend/`, and the
results are committed, so nobody installing adminsite needs Node. After changing the classes in a
template, rebuild the stylesheet, or the new classes will not exist:

```
cd frontend
npm install
npm run build
```

`npm run vendor` copies fresh HTMX, Alpine and Geist from `node_modules`.

## Rules the code keeps

[AGENTS.md](AGENTS.md) lists them, and they apply to people as much as to agents. In short: every
visible text is translated, layouts mirror for right to left languages, every control has an
accessible name, reads go through the view, a page never falls into N+1 queries, and code works on
SQLite, Postgres and MySQL.

## Commits and the changelog

Keep one change to a commit, with a message such as `feat: ...`, `fix: ...` or `docs: ...`. Add a
line to `CHANGELOG.md` under `## Unreleased`, starting that heading if it is not there, for
anything a user would notice, and update the docs page the change belongs to.
