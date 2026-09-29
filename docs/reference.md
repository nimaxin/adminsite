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
    options:
      members:
        - get_list_display
        - get_search_fields
        - get_filters
        - get_ordering
        - get_form_fields
        - get_readonly_fields
        - can_access_field
        - get_actions
        - get_inlines
        - get_column_choices
        - allows
        - scope_query
        - search_condition
        - before_save
        - after_save
        - after_save_committed
        - before_delete
        - after_delete
        - after_delete_committed
        - get_record_title

::: adminsite.RequestAction

::: adminsite.Inline

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

::: adminsite.actions.Input

::: adminsite.Message
    options:
      members: [update, delete, count, records, statement, covered_keys]

## Filters

::: adminsite.backends.sqlalchemy.SQLFilter

::: adminsite.filters.FilterOption

::: adminsite.filters.FilterValue

## Fields

::: adminsite.Field

::: adminsite.BaseField

::: adminsite.fields.ComputedField

::: adminsite.fields.EnumField

::: adminsite.fields.RelationField

::: adminsite.Html


::: adminsite.fields.JSONField

::: adminsite.fields.FileField

::: adminsite.fields.ImageField

::: adminsite.files.FileStorage

::: adminsite.files.LocalStorage
    options:
      members: [url, path_for]

## Pages, plugins and the dashboard

::: adminsite.AdminPage

::: adminsite.Plugin

::: adminsite.Widget

::: adminsite.Stat

::: adminsite.Chart

::: adminsite.RecentRecords

::: adminsite.ModelCounts

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
