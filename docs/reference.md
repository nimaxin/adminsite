# Reference

The classes you use most, with their signatures and docstrings.

## The admin

::: adminsite.Admin
    options:
      members: [add_view, routes, middleware]

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
        - get_actions
        - allows
        - scope_query
        - before_save
        - after_save
        - before_delete
        - after_delete
        - title_of

::: adminsite.views.writing.SaveContext

::: adminsite.views.writing.DeleteContext

## Actions

::: adminsite.actions.action.action

::: adminsite.actions.Selection
    options:
      members: [update, delete, count, records, statement, covered_keys]

## Filters

::: adminsite.backends.sqlalchemy.SQLFilter

::: adminsite.filters.FilterOption

::: adminsite.filters.FilterValue

## Signing in

::: adminsite.auth.AuthProvider

::: adminsite.auth.PasswordAuth

::: adminsite.auth.hash_password

## Audit log

::: adminsite.audit.AuditLog
    options:
      members: [history, recent, record, close]

::: adminsite.audit.AuditEntry

## Errors

::: adminsite.RefusedError

::: adminsite.PermissionDeniedError

::: adminsite.RecordNotFoundError
