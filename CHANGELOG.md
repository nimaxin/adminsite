# Changelog

## Unreleased

- A new look. The admin sits on a quiet ground with each page on a raised panel, set in Geist,
  which now ships inside the package with no request to any other site. The sidebar gets icons,
  search at the top, and an active page you can read in light mode as well as dark. Colours
  come from two daisyUI themes, so a project can still restyle the admin with its own.
- The list reads better. Saved views are tabs above it instead of a menu. Each filter is a chip
  that opens a small form of its own, and a range is typed into two fields, or picked from
  presets, instead of one box that wanted `from,to`. A choice such as a status is a coloured
  badge, in a tone that follows its place among the choices so it never changes between pages.
  A row's actions and its form sit in one menu at its end, ticked rows raise a bar of actions
  over the table, and the pager numbers its pages.
- A record's page has a title with its status beside it, its actions as buttons and delete in a
  menu, its details in two columns, and the records it links to one by one as cards beside
  them, opening the linked record's own page. The history's latest entries sit there too.
- The form lists every problem at the top, each a link to its field, as well as beside the field,
  so a mistake at the bottom of a long form is never missed. Fields sit two to a row where they
  are short, a value that cannot be changed reads as text instead of a greyed out box, and an
  inline row is removed with a bin button instead of a red box. `Inline.extra` blank rows now
  appear only while a table has no rows, so editing a record no longer shows an empty line under
  its children.
- The overview's cards share the new look. A chart gets lines across it at round numbers, an axis
  that says what they are, its tallest bar picked out, and each bar's value on hover. A day that
  SQLite hands back as text reads as "Sep 14" like any other date. The latest records card links
  to the whole list.
- On a phone the list becomes a column of cards, each with its first column and badge on top and
  the rest below, its amount at the end. A page's secondary actions fold into one menu so the
  header keeps to one line, the form's title gets the whole line, and the overview's cards sit
  two to a row.
- Every field takes `format`, such as `format="€{:,.2f}"`, for how its value is written in the
  list, on the record page, in the export and on the overview. The form keeps the plain number.
  The sign in page, error pages, the activity page, imports and action dialogs share the new look.

## 0.1.0a5

A security release. A review of every route found ways to read or link records outside a
view's scope, to sort by columns the view hides, to post to an admin without a secret key from
another site, and to tell usernames apart by how long a sign in took. All are closed. Anyone on
0.1.0a4 or earlier should upgrade.

- **Security.** A link could be set to a record its own view would not show. The form's picker
  offered only records within the target view's scope, but the key that came back was loaded by
  itself, so a user scoped to one region could attach an order to a customer of another, and learn
  from the answer whether a key existed. A key is now resolved through the target's own view, on
  the form, in inline rows and in the API, and a key it will not give up is refused like any other
  bad choice, with nothing said about whether the record exists.
- **Security.** `?sort=` took any column, including one left out with `exclude` and columns of a
  linked model, which put the rows in the order of a value the user could not see. A sort asked for
  in the URL now has to name a column the user can read somewhere on the view.
- **Security.** An admin without a `secret_key` has no session, and so had no form token, which
  left it open to a form posted from another site through the browser of anyone on the same
  network. Without a session, a post is now refused when the browser says it came from elsewhere,
  through `Sec-Fetch-Site` and `Origin`. A request that says nothing, from a script, goes through
  as before.
- `PasswordAuth` answered an unknown username at once and a known one after 600,000 rounds of
  hashing, so the time a sign in took said which usernames exist. An unknown name now costs the
  same as a wrong password, and the hashing runs on a thread instead of holding up every other
  request.
- Signing in starts a fresh session, so nothing planted in the browser before it, the form token
  included, is worth anything afterwards. Signing out ends the whole session. A script that takes
  the token from the login page has to take it from a page drawn after signing in instead.
- An action that `get_actions` leaves out for this user can no longer be run by name; asking for
  it is a 404, as is asking for an action that does not exist at all, which used to be a crash.
- The CSV export marks a cell that a spreadsheet would run as a formula, one starting with `=`,
  `+`, `@` or a tab, as text with a leading apostrophe. Numbers are left alone.
- The buttons of a record action were broken markup, since the record's key was written into a
  double quoted attribute in double quotes. They are drawn in single quotes now.
- A `before_save` that refused a change to an existing record crashed on an async engine, because
  the form was drawn again from a record the rollback had expired. The record is loaded again
  first.
- A bulk action on a table whose key is two columns acted on the wrong rows: the rows were matched
  by the first column of the key alone, so relabelling one line of an order relabelled every line
  of it, and deleting one deleted them all. Rows are matched by the whole key now, and the log
  names the whole key.
- A path in the URL that names no field, in the lookup or the file route, is a 404 instead of a
  crash. The JSON API writes a `ChoiceField(multiple=True)` from a list. A kept import file is
  readable by the server's own user alone.

- **Security.** A relation picker read the other model's table directly, so it ignored that model's
  view: it offered records outside `scope_query`, offered them to users whose permissions denied
  the view entirely, and searched every text column rather than the ones on show. A picker now
  reads through the target's own view, and its lookup needs the permission that opens a form.
  Anyone running 0.1.0a4 or earlier with a `scope_query` or a denied view should upgrade.
- A picker's search looks in the target view's `search_fields`, and where it names none, in the
  text columns its records are named by. A target with neither cannot be narrowed by typing.

## 0.1.0a4

The fourth alpha. It closes the gaps the first ports of real panels ran into: actions on one
record and on the view, computed fields, JSON columns, markup in a cell, lighter field
overrides, a relation picker that holds many records, and settings for the pages a view does
not need. Tested on SQLite, Postgres and MySQL.

- `AuthProvider.sign_in_failed(request, username)` runs on every wrong password and returns
  what the login page says, so an attempt can be recorded, alerted on or slowed down, and the
  message is no longer fixed.
- Action inputs can start with a value, `default=True`, hold several options,
  `ChoiceField(multiple=True)`, and be worked out per request, since the run now goes through
  `get_actions` too. `default` works on any field, so a form for a new record starts from it.
- `deferred_fields` leaves heavy columns out of the list query, so a table carrying a large
  JSON payload no longer loads it on every row. The record page, the form and the API load
  them as usual, and a column the list shows is never left out.
- A cell can hold markup. A field that returns `Html` writes it into the page as it is, so a
  column can link to a related record, a file or another system. Values put in with `format`
  are escaped, and the CSV export and the JSON API send the text without the tags.
- The CSV export was missing computed columns. It wrote the columns the query loaded rather
  than the columns the list shows, and a computed column loads nothing of its own.
- `FieldOptions("name", label="Product name")` in a view's `fields` changes one thing about
  the field adminsite worked out, without naming its type or its target again. An option the
  field does not take is an error naming the path, not a setting that does nothing.
- A field that says `readonly=True` is now treated as readonly by the form, the API and the
  import, as `readonly_fields` already was. A primary key is the exception: a form that names
  one means to set it.
- A relationship holding many records can be edited on a large table. Above 100 records the
  picker became a search box that kept one key, which lost every other record the link held.
  It now keeps all of them, showing each as a chip with a button to take it off.
- `can_export = False` switches the CSV export off for a view, and the button now follows
  `Permission.EXPORT` instead of always showing. `can_detail = False` switches off the record
  page: rows open the form instead, and saving lands on the list.
- `page_sizes` offers a rows per page menu above the list. The choice is kept in the URL and the
  session, stays while paging, sorting, searching and filtering, and only a size the view offers
  counts.
- `before_save` can change what is stored: whatever it leaves in `context.values`, or writes with
  `context.set(path, value)`, is what is applied, so a slug or a derived value is stored instead of
  what was submitted. The caller's own values are left alone.
- `RefusedError("...", field="delay")` puts a refusal next to that input instead of above the form,
  keeping everything else that was typed. The API answers 422 with the field, and 409 for a
  refusal about no field in particular.
- JSON columns get a `JSONField`: readable on one line in the list, laid out in a box on the form,
  and a malformed document comes back as an error on the field instead of being stored as text.
- A value that fails to parse comes back into its input as it was written. Before, the field fell
  back to the stored value and quietly threw away what the person had typed.
- Actions can run on one record, `on="record"`, appearing on its row and on its page with the
  record's own permission deciding, or on the whole view, `on="view"`, with nothing ticked. Both
  keep the dialog, inputs, confirmation, messages and audit that selection actions have.
- An action can return a response instead of a message, so it can hand back a file or JSON.
- `Computed` shows a value the view works out from a record, in the list, on the record page and
  in the export. `needs` names what the function reads so it is loaded with the page, and the
  value is never written, sorted or filtered.
- `Field.text_for(record, value)` gives a field the record its value belongs to, so an amount can
  read with its currency or a status with the column beside it. Everything that shows a value goes
  through it, and it falls back to `display`.
- `detail_fields`, and `get_detail_fields(request, record)`, say what the record page shows when
  that differs from the form. The page follows the form unless you name them.
- A view's `icon` is drawn in the sidebar. It takes inline SVG markup or the address of a picture.
- A filter on a path through a relationship shows its options without counts instead of raising.
- `session_https_only` and `session_max_age` on `Admin` for the session cookie.

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
