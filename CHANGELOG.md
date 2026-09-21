# Changelog

## Unreleased

- The UI is rebuilt on daisyUI, with light and dark themes, a drawer sidebar on phones, and one
  template per form widget.
- A foreign key column shows as its relationship in the default list and form.
- Tested on Postgres with asyncpg and psycopg, and CI runs the suite against Postgres.
- Keys read from URLs and filters are converted to the column type. Postgres refused to compare an
  integer key with text, which broke the detail, edit and delete pages there.
- Deleting a record that others still refer to, or saving a value that must be unique, now shows a
  message instead of an error page.

## 0.1.0a1

The first alpha. The shape of the API may still change before 0.1.0.

- Read a model and describe it without any ORM detail, so another ORM can be supported later.
- Fields that display, edit and convert values, chosen from the column types.
- One async interface over async and sync SQLAlchemy sessions.
- List pages with search across dotted paths, filters with counts, sorting and paging, and no
  N+1 queries.
- Filters you can write yourself.
- Create, edit, detail and delete pages, with links picked from a list or searched.
- Permissions at view, action, field and row level.
- Hooks that run inside the transaction and can refuse a save.
- Bulk actions over the chosen rows or over every row matching the filter.
- CSV export of the filtered list.
- Signing in, and a CSRF token on every form.

`PasswordAuth` takes hashed passwords. Use `adminsite.auth.hash_password` to make one.
