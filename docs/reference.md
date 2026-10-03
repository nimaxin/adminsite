# Reference

The classes you use most, with their signatures and docstrings.

## The admin

::: adminsite.Admin
    options:
      members:
        - add_view
        - add_page
        - use
        - add_route
        - add_static
        - add_template_dir
        - add_stylesheet
        - add_script
        - render_template

## Views

::: adminsite.ModelView

::: adminsite.RequestAction

::: adminsite.Link

::: adminsite.ColumnReference

::: adminsite.Descending

::: adminsite.Inline
    options:
      members: false

::: adminsite.PanelWidget

::: adminsite.FieldsetWidget

::: adminsite.RowWidget

::: adminsite.TabsWidget

::: adminsite.CountMode

::: adminsite.Pagination

::: adminsite.SaveContext

::: adminsite.views.SaveValues

::: adminsite.views.SaveValue

::: adminsite.DeleteContext

::: adminsite.Statement

## Actions

::: adminsite.actions.action.action

::: adminsite.actions.Selection
    options:
      members: [view, session, request, keys, everything, update, delete, count, records, statement, covered_keys]

::: adminsite.actions.Input

::: adminsite.Message
    options:
      members: [stays]

## Filters

::: adminsite.backends.sqlalchemy.SQLFilter
    options:
      inherited_members: true
      members: [multiple, template, options, condition, apply]

::: adminsite.filters.FilterOption

::: adminsite.filters.FilterValue

## Fields

::: adminsite.Field
    options:
      members: [column_types]

::: adminsite.BaseField
    options:
      members:
        - name
        - widget
        - python_type
        - error_message
        - stored
        - keeps_value_when_blank
        - unused_options
        - display
        - text_for
        - hint
        - serialize
        - parse
        - to_python
        - check_options

::: adminsite.fields.ComputedField
    options:
      members: false

::: adminsite.fields.EnumField
    options:
      members: false

::: adminsite.fields.RelationField
    options:
      members: false

::: adminsite.Html

::: adminsite.fields.JSONField
    options:
      members: false

::: adminsite.fields.FileField
    options:
      members: false

::: adminsite.fields.ImageField
    options:
      members: false

::: adminsite.files.FileStorage

::: adminsite.files.LocalStorage
    options:
      members: [url, path_for]

## Pages, plugins and the dashboard

::: adminsite.AdminPage

::: adminsite.Plugin

::: adminsite.Widget

::: adminsite.Stat
    options:
      members: false

::: adminsite.Chart
    options:
      members: false

::: adminsite.RecentRecords
    options:
      members: false

::: adminsite.ModelCounts
    options:
      members: false

## Saved views

::: adminsite.SavedViews
    options:
      members: [visible_to, save, delete]

## Translations

::: adminsite.i18n.gettext

## Signing in

::: adminsite.auth.AuthProvider

::: adminsite.auth.PasswordAuth

::: adminsite.auth.hash_password

## Audit log

::: adminsite.audit.AuditLog
    options:
      members: [find, history, recent, record, close]

::: adminsite.audit.AuditStore

::: adminsite.audit.AuditQuery

::: adminsite.audit.AuditEntry

## Errors

::: adminsite.RefusedError

::: adminsite.PermissionDeniedError

::: adminsite.RecordNotFoundError

::: adminsite.exceptions.IntegrityError
