# JSON API

The admin can answer in JSON too, for scripts, other services or a front end of your own. It reads
and writes through the same views, so permissions, `scope_query`, hooks and the audit log all apply
as they do on the pages.

The examples on this page use the [example models](models.md).

```python
admin = Admin(engine, views=[OrderView, CustomerView], api=True)
```

Everything lives under `/admin/-/api`.

| Request | What it does |
|---|---|
| `GET /-/api` | The views this user may open, with their fields and actions. |
| `GET /-/api/orders` | A page of orders. |
| `POST /-/api/orders` | Add an order. Answers 201 with the new record. |
| `GET /-/api/orders/12` | One order. |
| `PATCH /-/api/orders/12` | Change the fields sent, and only those. |
| `DELETE /-/api/orders/12` | Delete the order. Answers 204. |
| `POST /-/api/orders/actions/ship` | Run an action. |

## Reading

A page takes the same query as the list page: `q` to search, `sort` such as `-created_at`, the
filters by name such as `status=SHIPPED`, `page`, and `limit` up to 500.

```json
{
  "items": [
    {"key": "12", "id": 12, "customer": "3", "customer.name": "Aisha Khan",
     "status": "shipped", "total": "107.00", "created_at": "2026-09-01T10:30:00"}
  ],
  "total": 24,
  "estimated": false,
  "page": 1,
  "has_next": true,
  "next": null,
  "previous": null
}
```

A record carries the fields the list, the record page and the edit form show this user, each under
its name:

- A relationship comes as the key of the linked record, and a link to many as a list of keys.
- A decimal comes as a string, so no cents are lost.
- Dates and times come in ISO format, and datetimes in UTC with their offset, such as
  `2026-09-01T10:30:00Z`.
- A file comes as its name and its address.

With [keyset pagination](views.md#large-tables), `page` is null, and `next` and `previous` are
cursors to pass back as `after` and `before`.

## Writing

`POST` and `PATCH` take a JSON object of paths and values, checked by the form's fields. A `POST`
writes what the create form does and needs every required field. A `PATCH` writes what the edit
form does, and only the fields it sends. Problems come back per field, with status 422:

```json
{"error": "Some fields need another look.",
 "errors": {"total": "Enter an amount, for example 12.50.", "colour": "This field cannot be written."}}
```

- A read-only field is never written, so a `PATCH` that sets one answers 422.
- A [form-only field](fields.md#form-only-inputs), such as a password, can be written and is never
  sent back.
- A datetime sent without an offset is read in the sender's time zone, which for a token is the
  admin's [`timezone`](translations.md#time-zones).
- Files are uploaded through the pages, not the API.
- A hook that refuses, or a delete other records depend on, answers 409 with the reason.

The answer is the record as this user now reads it. A change that takes the record out of their
`scope_query` is still saved, and answers with its key alone: `{"key": "12"}`.

## Actions

```http
POST /admin/-/api/orders/actions/ship?status=PAID
Content-Type: application/json

{"keys": ["12", "13"], "inputs": {"carrier": "DHL"}}
```

Send `"everything": true` instead of keys to run the action over every record the query matches.
The answer is the action's message, `{"message": "2 orders marked as shipped."}`, or the file or
other response the action answers with.

An input that asks for [another record](actions.md#another-record) takes its key, and the fields of
a [group](actions.md#a-group-of-values) go by their full name, `{"change.percent": "5"}`. An
[action on one record](actions.md#on-one-record) takes its one key, and an action on the view
takes no keys. An action that asks for a file runs from the pages only.

## Signing in

Without `auth` the API is as open as the admin. With `auth`, a request needs one of two things:

- **The session** of someone signed in to the admin, as a front end in the same browser has. A
  request that changes something then sends the form token in an `X-CSRF-Token` header. The token
  is in the `_csrf` field of any admin form.
- **A token** in `Authorization: Bearer <token>`, for scripts and services. Nobody gets in this way
  until your auth provider says who a token belongs to:

```python
from sqlalchemy import select

from adminsite.auth import AuthProvider


class StaffAuth(AuthProvider):
    async def authenticate_token(self, token: str) -> User | None:
        async with session_factory() as session:
            return await session.scalar(
                select(User).join(ApiKey).where(ApiKey.token == token, ApiKey.active)
            )
```

A request with neither gets a 401, and anything a permission refuses gets a 403, always in JSON.
