# Changelog

## Unreleased

- Inlines: edit a record's children in its own form, such as an order's lines, with
  `inlines = (Inline("items"),)`. Rows can be added, changed and removed, all saved in one
  transaction with the parent, and the detail page lists them.
- Keyset pagination for big tables: `pagination = Pagination.KEYSET` moves by cursor, so deep
  pages cost the same as the first. Sorts it cannot use fall back to page numbers.
- `CountMode.ESTIMATED` shows the table estimate Postgres and MySQL keep, and stops a narrowed
  count at 10,000.
- The primary key breaks ties in every sort, so rows no longer repeat across pages when a sorted
  column has equal values.
- Changing the search, a filter or the sort goes back to the first page.
- Several admins in one app keep separate sessions by default: the session cookie is named after
  the admin's title unless you set `session_cookie`.
- Action and delete buttons are hidden from users that `allows()` refuses.

## 0.1.0a2

The second alpha. If you use Postgres, upgrade: 0.1.0a1 could not open records there.

- Audit log. `audit=True` records who created, changed or deleted what, with each field's value
  before and after, in a SQLite file of its own; pass an `AuditLog(engine)` to keep it in your own
  database. Records get a History tab and the admin gets an Activity page. Bulk actions write one
  entry per affected record, grouped by a batch id, the way Laravel Nova does. Entries are written
  only after the change commits.
- Actions can ask for values in a dialog before they run, checked like form fields and passed to
  the method by name. Confirmations and deleting a record use a dialog instead of the browser's
  confirm box.
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
