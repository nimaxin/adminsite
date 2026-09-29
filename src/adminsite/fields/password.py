from dataclasses import KW_ONLY, dataclass
from typing import Any

from adminsite.fields.scalars import StringField


@dataclass(eq=False, repr=False)
class PasswordField(StringField):
    """A password to set: typed in, never shown, changed only when typed.

    ```python
    class UserView(ModelView[User]):
        fields = [
            User.email,
            PasswordField("password", required=True, exclude_from_detail=True),
        ]

        async def before_save(self, context: SaveContext) -> None:
            password = context.values.get("password")
            if password:
                context.set("password_hash", hash_password(password))
    ```

    It is form only: what is typed reaches the hooks, never the record, and
    the input is never filled in. `required` holds for a new record; on an
    existing one, leaving it empty keeps the password it has.
    """

    _: KW_ONLY
    form_only: bool = True
    # Asked for by an action, it is kept out of the audit log too.
    secret: bool | None = True

    widget = "password"
    blank_keeps = True

    def parse(self, raw: str | None) -> Any:
        """The password as typed. Spaces around it are part of it.

        Only spaces count as nothing typed, so a stray space never becomes
        a password.
        """
        if raw is None or not raw.strip():
            return super().parse(None)
        return self.to_python(raw)

    def serialize(self, value: Any) -> str:
        """Nothing: a password is never written back into the page."""
        return ""

    def display(self, value: Any) -> str:
        """Nothing: a password is never shown."""
        return ""
