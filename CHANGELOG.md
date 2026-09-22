# Changelog

## 0.1.0a3

The third alpha. It adds everything the first projects
asked for: inlines, big-table paging, saved views, a command palette, pages and plugins, a
dashboard, file fields, CSV and Excel import, a JSON API, Persian and right to left layouts, and an
accessibility pass. Tested on SQLite, Postgres and MySQL.

- Tested on MySQL 8 with aiomysql and pymysql, and CI runs the suite against MySQL. Keyset
  pagination falls back to page numbers when a list is sorted by an enum column, since MySQL sorts
  and compares native ENUMs differently and the next page would skip rows.
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
- Accessibility: a skip link, named landmarks and `aria-current` in the sidebar; captioned tables
  with `aria-sort`; row checkboxes named after their record; inputs named by their label and tied
  to their error or help; problems that stay until closed; the new count announced after the
  list updates; stronger contrast for dimmed text. A test checks every control has a name.
- Translations: `language="fa"` puts the whole admin in Persian with a mirrored right to left
  layout, and `languages=[...]` adds a menu, picking the browser's language on the first visit.
  Your own `translations` override or add to the shipped catalogs, and `adminsite.i18n.gettext`
  translates your own messages.
- JSON API: `api=True` serves every view at `/-/api` for listing, reading, adding, changing,
  deleting and running actions, through the same permissions, scopes, hooks and audit log as the
  pages. Scripts sign in with a bearer token checked by `AuthProvider.authenticate_token`; a
  browser session has to send the form token in `X-CSRF-Token` to change anything.
- Importing from CSV or Excel with a preview: switch it on with `can_import = True`. Rows with a
  known key change that record, rows without one add a record, and every row is checked by the
  form's fields before anything is written. The good rows are saved through the view, so hooks,
  permissions and the audit log apply. Excel needs `adminsite[excel]`.
- Two messages in a row are both shown. The second used to be lost, since the session was only
  saved when a key was set, not when its list grew.
- File and picture fields: `FileField` and `ImageField` store uploads through a `FileStorage`,
  `LocalStorage` built in. Files are served behind the sign in, with anything but pictures and
  PDFs sent as a download; pictures are checked by their bytes. Replaced files are deleted after
  the commit, and a failed save throws its new file away.
- Every endpoint closes the request's form when it is done, so uploads never leave temporary
  files open.
- Dashboard: the overview takes cards with `dashboard=[...]`: `Stat` with an optional change on
  the period before, `Chart` drawn as SVG on the server, `RecentRecords` through a view's
  permissions, `ModelCounts`, or a `Widget` of your own. A failing card is shown as failed and
  logged instead of breaking the page.
- Pages of your own: subclass `AdminPage` with a template and a context, and it gets the admin's
  layout, a place in the sidebar and the palette, its own permission check and form handling.
- Plugins: a `Plugin` adds views, pages, routes under `/-/`, template folders, static files,
  stylesheets and scripts. The same `add_*` methods work on the admin directly.
- Command palette: Ctrl+K or Cmd+K jumps to any page, or to a record found by each view's own
  search, within the user's permissions and scope. `global_search = False` leaves a view out.
- `Permission.HISTORY` controls the History tab and a view's entries on the Activity page. The
  Activity page shows only views this admin registers and the user may read, filtered in the
  query so the latest readable entries always show.
- A refused page is a 403 "Not allowed" page inside the admin instead of a server error, and
  missing pages get the same layout. The sidebar leaves out views the user may not open.
- Column picker: people hide and show columns from a Columns menu, offered from `list_display`
  and `list_columns`. The choice is kept in the URL and the session, and the export follows it.
- Saved views: `saved_views=True` lets people keep a search, filters, sort and columns under a
  name, for themselves or shared with everyone. Stored in `adminsite_views.db` by default, or in
  your database with `SavedViews(engine)`.
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
