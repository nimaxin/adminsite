# Changelog

## Unreleased

- A choice filter with more than 10 options has a search box above them, which narrows them to
  those whose label holds what was typed. Ticked options stay in view while it does (#44).
- The foot of the sidebar is one account menu: the name of whoever is signed in opens it, upwards,
  with a Dark mode switch, the language and the time zone, each opening its own menu beside it,
  and Sign out. They sat side by side before, and crowded the name out. Without signing in, the
  button reads Settings. A project that overrides `base.html` should start its copy again from the
  new one.
- The list's filters sit behind one Filter button, and each applies as it is picked: ticking a box
  or choosing a range reloads the list at once, with no Apply button. The filters in use show as
  chips under the toolbar. A chip opens its filter again, its cross takes it off, and Clear all
  takes every filter off but keeps the search and the sort. A project that overrides
  `_toolbar.html` or `_table.html` should start its copy again from the new one (#46).
- Changing the columns or the rows per page keeps the sort, the search and the filters. They were
  lost once the table had reloaded on its own, after a sort or a search (#46).
- The actions for the ticked rows sit in an Actions menu above the list, which counts the ticked
  rows and offers to select every matching one. Dangerous actions come last, under a line. It
  replaces the bar that floated over the table. A project that overrides `_table.html` or
  `_row.html` should start its copy again from the new one (#46).

## 0.1.0a14

Times show on the clock of the person reading them: the browser says which time zone it is set to,
every datetime is shown in that zone, and a time typed in is read in it. A menu can offer other
zones, and a column declared `DateTime(timezone=True)` keeps the moment meant.

Before upgrading:

- A project whose database keeps local times rather than UTC should pass that zone as
  `database_timezone`, or its times will show shifted.
- A client of the JSON API that reads datetimes strictly should expect an offset after each one,
  such as `2026-09-01T10:30:00Z`.
- A project that overrides `base.html`, `login.html`, `detail.html` or `_history.html` should start
  its copy again from the new one. The browser's zone is saved by `_browser_timezone.html`, which
  `base.html` and `login.html` include.

What changed:

- Times show on the clock of the person reading them. The browser says which time zone it is set
  to, and a datetime is shown in that zone wherever it appears; a time typed into a form, the list,
  an import or the API is read in it. `Admin(timezone=...)` is the zone until the browser says,
  and `timezones` adds a menu at the foot of the sidebar to choose another (#42). It sits with
  the language menu on a row of their own, above the name of whoever is signed in.
- A datetime the database keeps without a zone is taken to be in UTC, unless
  `database_timezone` names another. A column declared `DateTime(timezone=True)` is given a time
  with its zone, so Postgres stores the moment meant, whatever zone its connection is set to.
- A time the clocks skip or repeat when they change is refused, rather than saved an hour off.
- History and Activity show when each entry happened on the reader's clock, no longer followed
  by "UTC". A changed time in the history reads as the log keeps it, in UTC, and says so. The date
  filter and the Activity page count the reader's days.
- The API sends each datetime in UTC with its offset, such as `2026-09-01T10:30:00Z`.
- Windows installs `tzdata`, since it has no time zone database of its own.

## 0.1.0a13

Values change straight from the list: a view's `inline_editable_fields` opens an editor under a
cell and saves it as the edit form does. The Activity page shows an entry about a record only to
someone who may read that record's history, where before reading the view's history was enough,
and actions take an icon.

Before upgrading:

- A `scope_query` that leaves nothing out for some users, such as superusers, should hand the
  statement back unchanged for them. The Activity page counts any other answer as leaving records
  out, and hides from those users the entries about deleted records and, by default, sign ins.
- A project that overrides `_table.html`, `base.html`, `list.html`, `detail.html`, `_inlines.html`
  or `widgets/readonly.html` should start its copy again from the new one. A list row is now drawn
  by `_row.html`, and an empty value by `_empty.html`.

What changed:

- The Activity page shows an entry about a record only to someone who may read that record's
  history, as its History tab does: the view's `scope_query` holds the record, and
  `allows(Permission.HISTORY, record=...)` says yes. It checked the view alone, so someone whose
  scope left records out read their changes there, or asked for one by its key. An entry about a
  deleted record shows only where `scope_query` leaves nothing out for the user, and by default
  so do sign ins.
- When the linked model has several views, a record page names and counts a link to many records
  as the list does: each record one of those views holds, linked to the first that holds it. It
  read through the first view alone, so records only another view held were left out (#36).
- An empty value shows as a grey hyphen in place of an em dash, now in the list too, where its
  cell was blank, and screen readers say "Not set". One template draws it,
  `adminsite/_empty.html`, so a project can change it through `template_dirs`.
- Every page draws a line under its header once it scrolls, so the rows that scroll beneath it
  no longer run into it.
- An action takes an `icon`, as a view does: inline SVG markup or a picture's address, drawn
  before its label in the bar over the ticked rows, above the list, in a row's menu and on the
  record page (#38).
- A view's `inline_editable_fields` names fields whose values change straight from the list: a
  cell opens an editor under it, which saves that one value as the edit form does, through
  `allows`, `can_access_field`, `get_readonly_fields`, the save hooks and the audit log, and
  draws the row again in place (#34).

## 0.1.0a12

The package is laid out the way Starlette's is: a module a project imports is named for what it
holds, and machinery starts with an underscore. A `JSONField` with `partial=True` shows only the
fields a record sets, with a box to add the others, and the Reference page documents every
parameter of the public API.

Before upgrading:

- Behaviour is unchanged, but these imports move, and the old paths stop working:

| Name | Was imported from | Now from |
| --- | --- | --- |
| `Database`, `SessionAdapter`, `Statement` | `adminsite.backends.sqlalchemy` | `adminsite.database` |
| `SQLAlchemyInspector` | `adminsite.backends.sqlalchemy` | `adminsite.inspector` |
| `SQLFilter`, `ChoiceFilter` and the other built-in filters, `SQLAlchemyRepository` | `adminsite.backends.sqlalchemy` | `adminsite.filters` |
| `Permission`, `RequestAction` | `adminsite.security` | `adminsite.permissions`, or `adminsite` as before |
| `add_message` | `adminsite.http.templating` | `adminsite.messages` |
| `Html` | `adminsite.text` | `adminsite.markup`, or `adminsite` as before |
| `SaveContext`, `DeleteContext` | `adminsite.views.writing` | `adminsite.views.contexts`, or `adminsite` as before |
| `QuerySpec`, `Page`, `Sort` | `adminsite` | `adminsite.query` |
| `FieldSchema`, `ModelSchema`, `RelationSchema` | `adminsite` | `adminsite.schema` |
| `ViewRegistry`, `SavedView` | `adminsite` | `adminsite.views`, `adminsite.saved_views` |
| `InvalidPathError`, `NotAModelError`, `UnknownFieldError` | `adminsite` | `adminsite.exceptions` |
| `ModelInspector` | `adminsite` | removed: `SQLAlchemyInspector` was its one implementation |

What changed:

- The Reference page documents every parameter, setting and return value of the public API,
  from the docstrings, so an editor shows the same when you hover over `Admin(`, `Field(` or a
  setting of `ModelView`. The guide pages link there instead of listing each option again.
- A `JSONField` with `partial=True` draws only the fields a record sets, each with Remove, and an
  Add a field box that finds the others by name or description, where it drew a Not set row for
  every field of the schema. The record page lists the fields left out on one line, Not set (48),
  which opens to name them.

## 0.1.0a11

A record page that reads like the list: a table of fields, each label beside its value. Forms in
one column with a save bar that stays in view, and `form_layout` for panels, rows and tabs. A
linked record that its own view's `scope_query` hides now shows as Hidden, where before its name
and values showed.

Before upgrading:

- A project that overrides `_table.html`, `detail.html`, `form.html`, `_field.html`,
  `_inlines.html`, `_values.html`, `widgets/readonly.html`, `_toolbar.html`, `_list_menus.html` or
  `import_preview.html` should start its copy again from the new one. They reach a view's fields
  through `view._fields` and its pages through `view._pages`, and the record page and the forms are
  drawn from the view's layout.

What changed:

- The record page lists its fields as a table: a row per field, the label beside the value, so the
  labels read down one column. A link to one record is a link in its row rather than a card beside
  the details, each record a link to many holds opens its own page, a yes or no always shows Yes or
  No, and a line under the title says who changed the record last when the audit log is on.
- The create and edit forms put one field on each line, in the order of `fields`, each input as wide
  as what it holds. A read-only value reads as plain text. The browser's own checks are off, so a
  missing value gets the admin's message in the admin's language, and after a failed save the page
  title starts with "Error:" and the list of mistakes takes the focus.
- A form's Save, Cancel and Delete sit in one bar at its foot, which stays in view while a long
  form scrolls; the buttons in the header are gone. On a phone each child row of an inline is a
  small group of fields, one under the other.
- `form_layout` arranges a view's forms and its record page, as starlette-admin's does: `PanelWidget`
  for a titled card, `FieldsetWidget` for a bordered group, `RowWidget` or a tuple for fields side
  by side, a list for a column and `TabsWidget` for tabs. A field the layout leaves out comes last;
  one placed twice, or not among the view's fields, stops the admin starting. The field gallery
  uses it (#33).
- The stylesheet's and the scripts' addresses end in a short hash of the file, so after an upgrade
  a browser fetches the new ones at once rather than keeping the old stylesheet with the new pages.
- A linked record that its own view's `scope_query` hides from the user now shows as "Hidden",
  with no name and no link, wherever another view shows it: the list, the record page, the form,
  the export and the API, which sends `null`. A column read through a link to many leaves out
  the records the linked view hides. Before, their names and values showed.
- adminsite no longer knows the names it used before 0.1.0a10. A view that sets one, such
  as `list_display`, starts and the setting does nothing, and an old class or keyword fails
  like any name that does not exist.
- `context.request` in the save and delete hooks, and `selection.request` in an action, are
  typed `Request` rather than `Request | None`: they always hold the request.
- The signed-in user is on `request.state.user`, as the permission examples in the docs read it.
  The sign in page no longer asks each view's `allows` while nobody is signed in.

## 0.1.0a10

Views written the way starlette-admin writes them, and typed: the model as the view's type
argument, one `fields` list, columns named by attribute, actions that ask for typed values, and a
check of every name when the admin starts. A JSON column can be edited as a form built from its
schema.

Before upgrading:

- Every view needs rewriting in the new shape, which the guides show. A view that still uses an
  old setting, keyword or class stops the admin when it starts, with what to write instead.
- A project that overrides `_table.html`, `detail.html`, `_toolbar.html`, `_list_menus.html`,
  `import_preview.html`, `_action_forms.html` or `dashboard/recent.html` should start its copy
  again from the new one. They call the view's methods by their new names, such as
  `view.get_record_title`, and an action's dialog draws its inputs, files included, with
  `_action_inputs.html`.

What changed:

- A view names its model as its type argument, `class OrderView(ModelView[Order])`, in place of
  `class OrderView(ModelView, model=Order)`, which now stops with the new form. `ModelView[Tag]` can
  be registered with no class of its own.
- Settings name columns by attribute as well as by string: `fields = [Order.id,
  Link(Order.customer, Customer.name)]`, `fields_default_sort = [Descending(Order.created_at)]`. An
  attribute of another model, a link that leads elsewhere, or one string where a list belongs stops
  the admin with a message naming the setting.
- `fields` lists a view's fields once, in order, for every page, as starlette-admin does. A page
  leaves one out by a flag on the field, such as `exclude_from_list=True`, or by a list on the view,
  such as `exclude_fields_from_export`; `hidden_in_list=True` keeps a column in the Columns menu but
  off the list until someone turns it on. Forms leave out columns of related models, computed
  fields, and keys the database or the model fills in, such as an autoincrement id or a `uuid4`
  default. `searchable_fields`, `sortable_fields` and `fields_default_sort` join them, and
  `Inline` takes attributes too: `Inline(Order.items, fields=[OrderItem.product])`. A view that
  lists no fields shows every column on every page, its key on the record page included.
  `sortable_fields` left out sorts by every column on the list, and `[]` sorts by none, as in
  starlette-admin.
- `list_display`, `form_fields`, `detail_fields`, `list_columns`, `exclude` and `readonly_fields`
  are gone: list the fields once in `fields`, leave one off a page with the `exclude_fields_from_`
  lists or flags, and give a field `hidden_in_list=True` or `read_only=True`. A computed field is
  shown and never edited, so no form has it any more, and `form_only=True` on one is refused.
  `get_list_display`, `get_form_fields`, `get_detail_fields` and `get_column_choices` are gone
  too: `can_access_field` decides who sees a field. So is `get_page_sizes`: list the sizes in
  `page_size_options`. A view that still sets or defines one of these stops the admin when it
  starts, saying what to write instead.
- A key people type, such as a code or the two columns of a composite key, is on the form for a
  new record, required, and read only once the record exists, so saving a form never moves a
  record to another key; a JSON `PATCH` that sets one answers 422. So is a link made only of key
  columns, such as the customer of a profile keyed by it. A key the database or the model fills in
  stays off both forms, and `read_only=True` on a key locks it on both, as in starlette-admin. If
  a hook changes a key, the edit page and the JSON API follow the record to its new key.
- Fields take the column they show and typed options, all keywords: `Field(Order.created_at,
  label="Placed")` is the field adminsite picks for the column, with your options, and a kind such
  as `DecimalField(Order.total, read_only=True)` chooses it. Whatever the options leave out comes
  from the column, so a field you write keeps its column's length, whether it may be empty, an
  enum's choices and a relationship's model. A type checker refuses a text field on a number
  column, a misspelt option, an option by position, a tone that does not exist, and a
  `ComputedField` written for another model. `EnumField(Order.status, choices=[...])` on an Enum
  column keeps the column's enum, the choices relabelling or narrowing its members, and
  `EnumField(Customer.region, enum=Region)` on a string column stores each member's value, as
  starlette-admin does.
- A field written in a view without `required` now takes it from its column, as the field adminsite
  picks does, where it used to mean not required. Give `required=False` to keep a field optional.
- `TextField`, `ChoiceField` and `Computed` are now `TextAreaField`, `EnumField` and
  `ComputedField`, with starlette-admin's names; importing an old name raises an error naming the
  new one. `FieldOptions("price", format=...)` is `Field(Product.price, format=...)`, which places
  the field as well, and importing `FieldOptions` names `Field`. `readonly=` on a field is
  `read_only=`, `enum_class=` is `enum=`, `ComputedField`'s `needs` takes attributes, and
  `RelationField(Order.customer, view=CustomerView)` names its view by class as well as by name.
- A computed field's `format` is used; it was left out before.
- Every name in a view's settings is checked when the admin starts, strings as well as attributes:
  one the model does not have stops the admin with a message naming the view and the setting and
  listing the model's columns and relationships. So do a relationship or a computed field in
  `searchable_fields`, `sortable_fields` or `fields_default_sort`, a sort through a relationship
  holding many records, a field both hidden in the list and excluded from it, a choice field with
  nothing to choose from, a field kind that does not fit its column, such as
  `RelationField(Order.customer_id)`, an option the kind never reads, such as `max_length` on a
  date, an exclude list naming a field the view does not show, `RelationField(view=...)` naming a
  view the admin does not have, and a class that is not a mapped model.
- A tone given for a value a choice field does not have is refused when the view is built, once
  the column has given its choices, rather than when the field is written.
- `record_title = "Order #{id}"` names a view's records, in place of `display_template`, and
  `get_record_title(record)` in place of `title_of`. A name in braces the model does not have, in a
  view's title, an inline's or a link's, stops the admin with the model's columns listed, where it
  used to show as nothing. A title reads the model's own columns: a relationship in braces, such
  as `{customer.name}`, stops the admin too, since a page loads a record without its links.
- `can_access_field(request, field, action)` decides who sees which field on which page, as in
  starlette-admin: the list and its Columns menu, the record page, the create and edit forms and the
  export, each named by `RequestAction`. A refused field is never read back from a form, and is
  left out of the JSON API, the search box, the filters and their counts, sorting, the History tab
  and the Activity page, and a `RecentRecords` card. The JSON API and `?sort=` follow the list,
  the record page and the edit form.
- `get_readonly_fields(request, record)` takes attributes, `[Order.customer, Order.status]`, and a
  misspelt name it returns is refused rather than ignored. `can_view_detail` and
  `Permission.VIEW_DETAIL` replace `can_detail` and `Permission.DETAIL`, and `Permission.DETAIL`
  raises an error naming the new name.
- A field for a column of a related model is named by its path, `customer.email`, rather than by
  the column alone, whether adminsite works it out or `Field(Link(...))` gives it.
- An action asks for its values with typed parameters, as FastAPI does: `carrier: Literal["DHL",
  "UPS"]` is a select, `day: date` a date picker, `product: Product` a record to pick, a dataclass
  its fields under one heading, and `UploadFile` a file, which no action could ask for before. The
  values reach the method typed, and `Annotated[str, Input(label="Reason", multiline=True)]` words
  an input. A parameter typed `Request`, `AsyncSession` or `SessionAdapter` is handed the request or
  the session, and a record action's record goes to the first parameter typed with the model,
  before or after the `*`. A type no dialog can ask for stops the admin when it starts, naming the
  parameter. `inputs=` and untyped `(record, session)` methods still work, and when `get_actions`
  replaces an action's inputs, its other typed parameters are still asked for.
- A link to many records that is required, such as an action's `stockists: list[Supplier]` with no
  default, now needs at least one record chosen, as a choice of several already did.
- `Selection[Order]` names the model of the rows an action runs over, so `records()` returns
  orders. A bare `Selection` now needs its model under mypy's strict mode, as a bare `list` does.
  `records(paths=...)` takes columns as attributes too.
- A save hook takes `SaveContext[Order]`, so `context.record` is an order, and reads and changes
  each value by its column, typed as the column is: `context.values[Order.status].get()` and
  `context.values[Order.note].set(...)`. A column the save leaves as it is reads as the record has
  it. `context.values.get("name")` and `context.set("slug", ...)` are gone; a string still names a
  column, or an input that is no column, as `context.values["password"].get()`, and `set()`
  refuses any other name, as `get()` does. A link reads as its record, even when the linked model
  has no view. On a new record, a column the save leaves out reads as None until the insert fills
  in its default.
- `after_save_committed` and `after_delete_committed` run once a change has committed, for an email
  or a webhook that must only follow a change that was kept. An error in one is logged, and the
  change stands. `context.record` holds what was saved, with the values the database set in its
  own columns, even after `after_save` changed it, and stays loaded after the commit whatever the
  session factory says. Values the database sets on an inline's rows are not read back.
- `RefusedError(..., field=Order.note)` names the field by its attribute, or by `Link(...)`, as well
  as by its name.
- `scope_query` takes and returns `Statement`, so returning a new select in place of the one given
  is a type error, and `scope_query`, `search_condition`, `allows`, `can_access_field` and
  `get_readonly_fields` take a `Request`, never left out. `allows` takes `record=` too, None for a
  question about the whole view. An override typed `-> Select[Any]` needs `-> Statement` under
  mypy's strict mode.
- `SQLFilter[Order]` names the model a filter narrows, so its `condition` gets a
  `SQLAlchemyRepository[Order]`.
- More of starlette-admin's names: `list_filter` is `list_filters`, `page_sizes` is
  `page_size_options`, `bulk_delete` is `can_delete_selected`, `search_fields` is
  `searchable_fields` and `ordering` is `fields_default_sort`. `get_filters`, `get_search_fields`
  and `get_ordering` are `get_list_filters`, `get_searchable_fields` and `get_fields_default_sort`,
  and `form_values` is `form_only_values`. A view that still sets an old name, `display_template`,
  `title_of` or `can_detail` among them, stops the admin when it starts, with the name it has now.
- A setting from another admin, such as Django's `list_display` or `search_fields`, is refused with
  words for someone who never used an older adminsite: "OrderView sets list_display, a setting
  adminsite does not have."
- The per-request methods answer with what their setting takes: columns by attribute or string
  from `get_searchable_fields`, columns or filters from `get_list_filters`, and sorts such as
  `Descending(Order.created_at)` from `get_fields_default_sort`, each checked like the setting.
  They, `get_deferred_fields`, `get_inlines` and `get_actions` take a `Request`, never left out,
  and `get_inlines` takes the record too.
- A view's own machinery is private: `save`, `delete`, `fetch_page`, `display`, `field_for` and the
  other methods adminsite calls itself start with an underscore, as do `schema`, `repository` and
  `filters`. Completing a view's name offers its settings, its `get_` methods and its hooks, and an
  action named `save` or `delete` no longer replaces adminsite's own. A method named
  `delete_selected` no longer replaces the built-in delete of the chosen rows either, and stops
  the admin: set `can_delete_selected = False` and add an action of your own.
- `Inline(extra=1)` is `Inline(blank_rows=1)`, `Admin(fields=...)` is `Admin(field_registry=...)`,
  `RecentRecords(detail="total")` is `RecentRecords(value="total")`, `SignInRefused` is
  `SignInRefusedError`, and a field class's `blank_keeps` is `keeps_value_when_blank`. Each old
  keyword raises an error naming the new one.
- Every module lists what it offers in `__all__`, so an editor completing `adminsite.files` no
  longer offers `Path` or `re`.
- adminsite's types hold on SQLAlchemy 2.0 as well as 2.1, and CI now runs mypy and the tests on
  2.0 too.
- The guides, the README and the docstrings write views this way, and CI type-checks every example
  in them under mypy's strict mode. Examples that could not have run are fixed: the audit log is
  read through the `AuditLog` given to the admin, and a SQLite listener goes on a sync engine.
- `Inline(fields=...)` takes fields as a view's `fields` does, so `Field(OrderItem.added_at,
  read_only=True)` shows a value the row never edits; `readonly_fields=` is gone. A row follows a
  view's pages: an existing row has the edit page's fields, a new row the create page's, and the
  record page the detail fields, a `Link` among them. A row leaves out only what the inline's
  relationship fills in, so another link to the parent's model, such as a match's away team,
  stays in the rows, and a relationship with no link back no longer asks each new row for the
  parent's key. The parent's `can_access_field` decides a row's fields, asked by their path such
  as `items.unit_price`, and its `get_readonly_fields` locks one with `Link(Order.items,
  OrderItem.unit_price)`. A refused link shows in its row's cell, and a mistake in an inline's
  fields names the view and the setting. `Inline` and `RelationField` take `record_title` in place
  of `display_template`, as a view does.
- A `ComputedField` loader is typed as taking a `SessionAdapter`, so an editor completes
  `session.execute` in it.
- `JSONField("settings", schema=ShopSettings)` edits a JSON column as a form built from a Pydantic
  type or a JSON Schema: a switch for a yes or no, a number input that keeps to its limits, the
  picker for a few fixed values, tables for lists of objects and for maps. Saving checks the
  document with Pydantic and shows each problem beside its input; the record page names each value
  by its title, and the API checks documents against the same schema.
- A JSON field's schema can come from a function given the record, for a table whose rows hold
  documents of different shapes. On a new record the form draws the document again as the rest of
  the form changes, such as when a setting's key is chosen.
- `partial=True` on a JSON field with a schema lets each property be left unset, with Set and Clear
  beside it, and saves only the properties set, as an override keeps what it changes.
- A list's columns fill the table's width again, as they did before 0.1.0a9. A key, such as an
  order's number, now reads from the start like text rather than lining up on the right like an
  amount, so on a list of one or two columns it no longer lands in the middle of the page. Each
  row's menu stays right after its checkbox.
- A key with the wrong number of parts, such as `/shelves/A` for a key of two columns, answers "not
  found" rather than a server error, and a link's key with a part too many is refused rather than
  cut short.
- A save or a delete from the pages commits once. It committed a second time, with nothing left to
  commit.
- The counts beside a list's filters count only the rows `scope_query` lets the user see. They
  counted every row in the table, which told a user how many records they may not open.
- A change the database refuses, such as a second customer with the same email, raises
  `adminsite.exceptions.IntegrityError` from `SessionAdapter.transaction()` and `commit()`, with
  SQLAlchemy's error as its cause. Its message is worded for people and holds none of the
  database's text, so no refused value reaches a page or the audit log. A commit that fails is
  rolled back.
- A save, an action or a delete the database refuses says so and names the likely causes: a value
  that must be unique is taken, a required value is missing, or other records still refer to one
  being removed. A delete the database refuses for itself still says other records refer to it.
  A save used to blame a clash with another record, and an action or a delete other records
  referring to them, whatever the cause.
- An import that changes existing records follows their edit form. A column that form does not let
  this user change, through `exclude_from_edit`, `can_access_field` or `get_readonly_fields`, may
  only hold what the record already has, so an exported file imported back still goes through,
  and a column this user reads on no page may only be left empty. Any other value shows in the
  preview as "This field cannot be changed." and the row is skipped. A column only the edit form
  has can be imported into existing records.
- An import adds a record whose key people type, such as a country's code, when a row names a key
  no record has. A row that leaves that key blank, a file without its column, or a second row
  adding the same key shows as a problem in the preview, where it used to fail at the database or
  answer with a server error. A key the database fills in still has to name a record that exists.
- The JSON API runs an action on one record or on the view the way the pages do: a record action
  takes its one key, is handed the record and asks the record's own permission, and an action on
  the view is written to the audit log once. The index says what each action runs on, as `on`.
  An action that answers with a file sends the file, and an `inputs` that is not an object
  answers 422.
- The record page names the records of a link to many, and counts the rest, through the linked
  model's own view, so records its `scope_query` hides are neither named nor counted.
- A relationship on the list, such as `Order.customer`, and a column reached through a link to
  many no longer get a sort link, which answered with a server error. A column read through a
  link to many, such as `Link(Customer.orders, Order.total)`, shows each record's value however
  many links it goes through, and the JSON API sends the values as a list. A number answered with
  a server error, a column further along showed the wrong value, and the API answered with a
  server error.
- A JSON `PATCH` or `POST` that saves a record outside what `scope_query` lets the user read
  answers 200 or 201 with only the record's key. It saved the change, then answered with a server
  error.
- A foreign key whose database column has its own name, such as `mapped_column("personId",
  ForeignKey(...))`, shows as its relationship in a view's default fields, like any other foreign
  key, and a link held by the other model, such as a person's passport, no longer takes the place
  of the model's own key there. `RelationSchema.local_columns` holds attribute names rather than
  database column names, and the new `remote_columns` holds the linked model's.
- A key of two columns written as text, such as `A,1`, finds its record everywhere: a link to such
  a model with no view saves, and the JSON API answers 201 rather than 500 after creating such a
  record.
- A list, an export or a picker over a view with no sort goes by the record's key. It had no order,
  so on Postgres a page could repeat rows of another, and an export could repeat some rows and
  leave out others.
- Creating a record through a session factory you give, such as
  `Admin(async_sessionmaker(engine))`, no longer answers 500 once the record is saved.
- `after_save` can read a value the database set during the save, such as an `onupdate` time. On
  an async engine, reading one failed the save.
- `SQLAlchemyRepository.create` and `.update`, which nothing called, are gone.

## 0.1.0a9

A picker you type in, links kept in the order you give them, a code box for JSON, and a list that
keeps its columns and each row's menu together. The live demo gains a gallery of every field.

Before upgrading:

- A project that overrides `base.html`, `_table.html`, `_row_actions.html`, `detail.html`,
  `_inlines.html`, or the widgets `_picker.html`, `relation.html`, `select.html` or `json.html`
  should start its copy again from the new one: the picker, the JSON box and the list's row menu
  work differently now, and a search answers with list options.

What changed:

- In Chrome and Edge a select that holds several options, such as a link to many records of a
  small table, laid them out in a row, cut off and overlapping. It is a list again.
- A link to many records is always a picker: its records as chips, in the order they were chosen,
  and a list to search. For a table of a hundred records or fewer the whole list opens at once and
  narrows as you type; choosing a chosen one again lets it go. A choice of several options is the
  same picker. The record page names a link's records in the relationship's own order, where it
  went by their keys.
- `ordered=True` on a link to many, such as servers tried in turn, shows its records as a
  numbered list to put in order, by dragging or with buttons to move one up or down. When the
  order changes, saving writes the link's rows again in the order shown, for a relationship read
  in the order of its link table's serial id.
- The picker is one box to type in, where it was a box that said "3 chosen" and a Choose button
  that opened a second search box. Clicking the box or typing in it opens the list under it; the
  chosen records sit in the box as chips; the arrow keys, Enter, Escape and Backspace work as in
  any search field. A search on the server says when more records match than it shows. An ordered
  link adds from a box under its list, which leaves out what the list holds.
- In an inline row, a link to a table of over a hundred records showed its record as the model's
  bare text, such as `<Supplier object at 0x…>`, and in a row added with **Add a row** its search
  found nothing. It names the record as the form does, and searches in every row.
- A JSON field is edited in a code box: line numbers, colours, Tab and Enter that indent, brackets
  and quotes that close themselves, a Format button, and a check as you type that says what is
  wrong and where. The record page shows the whole document, laid out and coloured, each object
  and list folding on its line, with Fold all and Copy; it used to cut a document short at 120
  characters.
- A list's columns sit together from the start of the row, where a few of them spread across a
  wide screen, and a number could land in the middle of the page. Each row's menu comes right
  after its checkbox, in reach however many columns follow, where it was at the far end.
- A second example, `examples/fields.py`, puts every kind of field on one model, each saying which
  class it is. The live demo shows it as a Field gallery beside the shop, whose products now have
  tags.
- `AuthProvider.sign_in_values(request)` fills in the sign in form, for a public demo whose
  visitors all share one account. The live demo now opens with its details already in place.
- After a failed sign in the form keeps the username that was typed, and the cursor waits in the
  password box.

## 0.1.0a8

A fix for record pages that 0.1.0a7 broke, and an audit log that is saved with the change it
describes.

Before upgrading:

- With the audit log in your own database, a save now fails when the audit table is missing,
  where the entry used to be lost after the change was saved. Add the table to your migrations,
  or pass `create_table=True`, before you upgrade.

What changed:

- A record page answered 500 when a to-many link it shows held no records, or when a computed
  field on it needed a link the page also shows. Both broke in 0.1.0a7.
- An audit log kept in your own database, with `AuditLog(engine)`, is written in the same
  transaction as the change it describes, so the two are saved together or not at all. A log kept
  elsewhere, such as the default SQLite file, is still written after the change. When that write
  fails, a saved change, a sign in or a download no longer ends in an error page: the change stays,
  and the server log names the entries that were lost. A store of your own joins the transaction
  with `lives_in` and `record_within`.
- Work that waits on a commit, such as removing a file a save replaced, all runs even when one
  piece of it fails.

## 0.1.0a7

What a large back office asks of its admin day to day. Actions can ask for another record, delete
the chosen rows through each record's own hooks, and answer with a message that stays or holds a
value to copy. A form can hold inputs that are not columns, such as a password. A computed value
can come from one query for the whole page, a Postgres array is edited as a list, a status says
which colour each of its values is, and every record and link has a readable name, never a memory
address.

Before upgrading:

- Every view that allows deleting now has Delete in its selection bar. Set `bulk_delete = False` to
  leave it out.
- Records and links are named differently. A record whose model has no `__str__` reads "Order #12",
  a linked record reads as the view showing its model names it, and a picker puts a link's own
  `display_template` first. The export and new audit entries change with them.
- An action input named just `key` is now written to the audit log. Give it `secret=True` if it
  holds one.
- The JSON API reads a Postgres `ARRAY` column from a list. A string is read as one value per line.
- The inputs in an action's dialog have ids of the form `field-<action>-<name>`.
- A mistake in `FieldOptions` is an error when the view is created, where it waited for the first
  page that used the field.
- `ChoiceField.tone_of` answers `None` for a value drawn with no badge. A value that is not one of
  the choices answers grey, where it answered `None` and was drawn grey.

What changed:

- adminsite works with SQLAlchemy 2.1, which a fresh install now gets. It depends on
  `sqlalchemy[asyncio]`, since 2.1 no longer installs `greenlet` by itself and an earlier adminsite
  could not even be imported without it. A JSON column is still edited as a document, where 2.1's
  new answer for its type turned it into a text box. `SessionAdapter.execute` and a chart's query
  are typed for rows of any number of columns, as 2.1 types them column by column. The tests pass
  on SQLAlchemy 2.0 and 2.1.
- A choice or a yes or no says which colour each value's badge is, by name:
  `FieldOptions("status", tones={OrderStatus.FAILED: "rose", OrderStatus.DONE: "green"})`. A value
  left out is grey, and `None` draws no badge, so a risk flag can be a rose badge when it is set and
  nothing when it is not. One tone, such as `tones="grey"`, colours every value alike. Without
  `tones`, a choice still takes the colour of its place in the list.
- Pasting into a list's search box or a link picker's search box now searches. Both only reacted
  to keys being typed, so text pasted with the mouse found nothing until another key was pressed.
- An action can ask for another record. A `RelationField` among its `inputs` offers the records
  that model's view lets the user see, as a list, or as a search box above a hundred, and the
  method receives the chosen record. A key the user could not have picked is refused. The audit
  log names the record, and the JSON API takes its key. Each dialog's inputs have ids of their
  own, so two dialogs on one page that ask for the same name no longer clash.
- A linked record is named the same way everywhere: by the link's own `display_template`, or else
  as the view showing that model names it. List cells, the record page, the export and the audit
  log used the model's `__str__`, which reads `<Group object at 0x...>` for a model without one,
  while pickers used the other view's template. A picker now puts the link's own template first.
- A record is named by its view's label and its key, "Order #12", when the view has no
  `display_template` and the model no `__str__` of its own. The page heading, the row menu, the
  command palette and the audit log showed `<Order object at 0x...>`. A model's `__repr__` is no
  longer used as a name, since one written for a dataclass prints every column.
- `translations={"en": {...}}` changes the admin's own English wording, such as "Username" to
  "Email" on the sign-in page, without copying a template. English was never looked up before.
- A Postgres `ARRAY` column is edited as a list, one value per line, where it was a JSON document
  to type by hand. Each value is checked by the field its type calls for, and a mistake is named by
  its line. The list and the export show the values separated by commas, the JSON API takes a
  list, and an import takes values separated by commas. `ListField` does the same for a JSON
  column holding a list.
- A form can hold inputs that are not columns. A field given `form_only=True` is never read from
  the record or written onto it; its value reaches `before_save` and `after_save`, which store it
  where it belongs. A view's `form_values` gives these inputs their starting values, such as
  settings kept as rows of another table.
- `PasswordField`, a password input that is never filled in. On a record that exists, leaving it
  empty keeps the password there. The JSON API takes it and never sends it back.
- The audit log keeps `***` for a secret field in what a save or a delete changed: one named like
  `password_hash` or `api_key`, or given `secret=True`. A name with `key` in it now counts as
  secret only when it says what the key is for, as in `api_key` or `private_key`, so a `sort_key`
  is shown again.
- Adding a record through the JSON API to a view with a computed field failed with an error. It
  now answers 201 with the record.
- `Computed(name, load=...)` works a value out with a query that runs once for every record on the
  page, such as a count of related rows: an async function given the session and the records,
  answering with each record's value by its primary key. The list, the record page, the form, the
  export and the API all show it, and `default` covers a record the answer leaves out.
- Delete in the selection bar of every view that allows deleting. Each chosen record goes through
  the same permission check, `before_delete`, `after_delete` and audit entry as a single delete,
  in one transaction: when one is refused, nothing is deleted and the message names it. Switch it
  off with `bulk_delete = False`. `selection.records()` takes `paths=` to load links with the
  records.
- A relation can name the view its links open and its picker lists from, as
  `FieldOptions("owner", view="buyers")` or `RelationField(..., view="buyers")`, for a model shown
  by several views. Without it, a record's card for a record the first view leaves out opens the
  first view that holds it, where it answered 404.
- A link to many records on a record page names the first 20 and says how many more there are, in
  two small queries, where it loaded every linked record into one long line.
- A view's `search_condition(term, request=...)` decides what its search box matches, for example
  an exact, normalised phone number that an index can answer, where the usual search looks inside
  every search field. The list, its count, the export, "select all matching", the command palette
  and pickers all use it; returning `None` keeps the usual search.
- An action can answer with a `Message`: one that stays until it is closed, carries a link, or
  shows a value with a button that copies it, such as a new API key. The audit log keeps the text
  and never the value to copy. An action that answers with `Html` has its markup kept, where it was
  escaped before. The JSON API returns the link and the value as fields.
- `in_sidebar = False` leaves a view out of the sidebar, the command palette's list of pages and the
  overview's counts, for a view whose records are only opened from other records. Its pages, links
  and record search stay as they were.
- A filter that `get_filters` returns for one request, without `list_filter` naming it, now
  narrows the list. It was offered and could be picked, but changed nothing.
- `@action(..., audit_answer=False)` keeps an action's answer out of the audit log, for one that
  shows a secret once, such as a new API key. The entry still says who ran it, on what and when.

## 0.1.0a6

A new look, and an audit log that answers what gets asked of it afterwards. The admin is
redesigned throughout and fits a phone. The audit log now records sign ins, every action and
export, and what failed, names each person by a key that lasts and the address they came from,
pages and filters, and can live in a table of your own.

Before upgrading:

- The audit table gains the columns `user_key`, `ip`, `user_agent`, `error` and `inputs`, all of
  which may be empty. A table adminsite created adds them by itself; for one in your own
  migrations, generate a migration.
- `PasswordAuth.verify` raises `SignInRefused` for an unknown username or a wrong password, where
  it returned `None`.
- A record's History tab shows its latest twenty entries, and the Activity page the rest.

What changed:

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
- A checkbox's tick appears at once. daisyUI drew it over 0.4 seconds, which dragged when ticking
  a column of rows.
- `Admin(banner=...)` shows one line above every page and on the sign in page, such as
  "Staging: changes here are not real."
- In a short window the overview and the forms no longer make the whole page scroll. The chart's
  numbers for screen readers were hidden from sight but kept their full height, and text kept
  for screen readers low in a long form was placed against the page instead of the panel.
- Every audit entry says who acted by the key `AuthProvider.identity` returns, as `user_key`, as
  well as by the name shown, which it had already. It also keeps the IP address and the browser,
  and the History tab shows the address. The audit table gains the columns `user_key`, `ip`,
  `user_agent`, `error` and `inputs`, all of which may be empty. A table adminsite created adds
  them by itself; for one in your own migrations, generate a migration after upgrading.
- The audit log can live anywhere. The admin writes and reads it through `AuditStore`, two
  methods: `record` and `find`, which takes an `AuditQuery` with filters and a place to page from.
  A store of your own can keep the log in a table your application already has, and the History
  tab and the Activity page show its rows. `AuditLog` gains `find`; `history` and `recent` stay.
- The audit log keeps every sign in, failed attempt and sign out, with the address and browser.
  `verify` can raise `SignInRefused(reason, user=...)` to say why an attempt failed; the reason
  goes to the log, never to the person. `PasswordAuth.verify` now raises it, saying whether the
  username was unknown or the password wrong, where it returned `None` before. The Activity page
  shows these entries to people who may read every model's history, and
  `AuthProvider.may_read_sign_ins` changes who.
- Every action is audited: on one record, with the fields it changed; on the whole model, which
  wrote nothing before; and one that answers with a file, which returned before it was written
  down. Each entry keeps the values the action was run with, with secrets as `***` and files by
  name, type and size. A field's new `secret` option decides what is a secret, where a name such
  as `password` or `api_key` does not. An action that is refused, not allowed or fails is written
  down as failed with its reason, after its work is rolled back. Downloading a list as CSV is
  written down with its search and filters.
- The Activity page pages through the whole log, fifty entries at a time, where it stopped at the
  latest 200. It filters by kind of entry, person, days and record as well as by model, and keeps
  the filters in its address. A record's History tab shows the latest twenty, where it showed a
  hundred, and links to the rest on the Activity page.

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
