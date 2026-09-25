# JSON API

The admin can answer in JSON as well, for scripts, other services or a front end of your own. It
reads and writes through the same views, so permissions, `scope_query`, hooks and the audit log all
apply, exactly as they do on the pages.

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
| `POST /-/api/orders/actions/ship` | Run a bulk action. |

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

A record carries the paths of `list_display`, then those of the form. Links come as the key of the
linked record, links to many as a list of keys, a list column as a list, decimals as strings so no
cents are lost, dates and times in ISO format, and files as their name and address. With
[keyset pagination](views.md#large-tables), `page` is null and `next` and `previous` are cursors to
pass back as `after` and `before`.

## Writing

`POST` and `PATCH` take a JSON object of paths and values, checked by the form's fields. A `PATCH`
touches only the fields it sends; a `POST` needs every required field. Problems come back per
field, with status 422:

```json
{"error": "Some fields need another look.",
 "errors": {"total": "Enter an amount, for example 12.50.", "colour": "This field cannot be written."}}
```

Only the form's fields can be written, read only fields never. Files are uploaded through the form.
A [form-only field](fields.md#inputs-that-are-not-columns), such as a password, can be written and
is never sent back; a password sent empty in a `PATCH` keeps the one there.
A hook that refuses, or a delete other records depend on, answers 409 with the reason.

## Actions

```http
POST /admin/-/api/orders/actions/ship?status=PAID
Content-Type: application/json

{"keys": ["12", "13"], "inputs": {"carrier": "dhl"}}
```

Send `"everything": true` instead of keys to run the action over every record the query matches,
as the "select all matching" link does on the list page. The answer is the action's message:
`{"message": "2 orders marked as shipped."}`.

## Signing in

Without `auth` the API is as open as the admin. With `auth`, a request needs one of two things:

- **The session** of someone signed in to the admin, as a front end running in the same browser
  has. A request that changes something then has to send the form token in an `X-CSRF-Token`
  header, so another site cannot make the browser do it. The token is in the `_csrf` field of any
  admin form.
- **A token** in `Authorization: Bearer <token>`, for scripts and services. Nobody gets in this way
  until your auth provider says who a token belongs to:

```python
class StaffAuth(AuthProvider):
    async def authenticate_token(self, token):
        async with Session() as session:
            key = await session.scalar(select(ApiKey).where(ApiKey.token == token))
            return key.user if key and key.active else None
```

A request without either gets a 401. Anything refused by a permission gets a 403 in JSON, never a
page.
