"""The adminsite command: the admin's own users, made from a terminal.

adminsite users create nima --superuser
"""

import argparse
import asyncio
import getpass
import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from adminsite.admin import Admin
from adminsite.exceptions import AdminSiteError
from adminsite.users import AdminUsers, User
from adminsite.users.rules import password_problem

__all__ = [
    "PASSWORD_VARIABLE",
    "main",
]

# Where the admin is looked for when --admin does not say, as `fastapi dev`
# looks for the app: each file, and the module it imports as.
LIKELY = (("main.py", "main"), ("app.py", "app"), ("app/main.py", "app.main"))

# Where a script or a container gives the password, instead of typing it.
PASSWORD_VARIABLE = "ADMINSITE_PASSWORD"


class _Refused(Exception):
    """What went wrong, said to the person at the terminal."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the adminsite command and answer with its exit code."""
    arguments = _parser().parse_args(argv)
    try:
        code: int = arguments.run(arguments)
    except _Refused as refused:
        print(f"adminsite: {refused}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adminsite", description="Work with an admin from the terminal."
    )
    groups = parser.add_subparsers(dest="group", required=True, metavar="COMMAND")
    users = groups.add_parser(
        "users",
        help="the admin's own users, kept by AdminUsers",
        description="The admin's own users, kept by AdminUsers.",
    )
    commands = users.add_subparsers(dest="command", required=True, metavar="COMMAND")
    create = commands.add_parser(
        "create",
        help="add someone who may sign in, such as the first superuser",
        description=(
            "Add someone who may sign in, such as the first superuser. The "
            f"password is typed twice, or read from {PASSWORD_VARIABLE}."
        ),
    )
    create.add_argument("username", help="what they sign in with")
    create.add_argument(
        "--superuser",
        action="store_true",
        help="let them do everything, managing users and roles included",
    )
    create.add_argument(
        "--role",
        action="append",
        default=[],
        metavar="NAME",
        help="a role they hold; give it again for each one",
    )
    create.add_argument(
        "--admin",
        metavar="MODULE:NAME",
        help=(
            "where the admin is, such as main:admin; left out, the Admin in "
            "main.py, app.py or app/main.py"
        ),
    )
    create.set_defaults(run=_create_user)
    return parser


def _create_user(arguments: argparse.Namespace) -> int:
    """Add a user to the admin the arguments point at."""
    admin = _find_admin(arguments.admin)
    users = admin.auth
    if not isinstance(users, AdminUsers):
        raise _Refused(
            "this admin does not keep its users with AdminUsers, so there is "
            "nobody to add here. Give it auth=AdminUsers()."
        )
    password = _password()
    made = asyncio.run(
        _create(
            admin,
            users,
            arguments.username,
            password,
            superuser=arguments.superuser,
            roles=arguments.role,
        )
    )
    kind = "superuser" if made.is_superuser else "user"
    print(f"Added the {kind} {made.username}.")
    return 0


async def _create(
    admin: Admin,
    users: AdminUsers,
    username: str,
    password: str,
    *,
    superuser: bool,
    roles: Sequence[str],
) -> User:
    """Add the user, then let go of the database the admin opened."""
    try:
        if not await users.has_tables():
            raise _Refused(
                "the users' tables are not in the database yet. Run your "
                "migrations with user_metadata, or give AdminUsers "
                "create_table=True."
            )
        return await users.create_user(
            username, password, superuser=superuser, roles=roles
        )
    except AdminSiteError as error:
        raise _Refused(_sentence(str(error))) from None
    finally:
        engine = admin.database.engine
        if isinstance(engine, AsyncEngine):
            await engine.dispose()
        elif engine is not None:
            engine.dispose()


def _find_admin(where: str | None) -> Admin:
    """The admin --admin names, or the one in the likely files."""
    # The project's modules import as the app imports them, from here.
    here = str(Path.cwd())
    if here not in sys.path:
        sys.path.insert(0, here)
    if where:
        module_name, _colon, name = where.partition(":")
        if not module_name or not name:
            raise _Refused("give --admin as MODULE:NAME, such as main:admin.")
        found: Any = _import(module_name)
        for part in name.split("."):
            found = getattr(found, part, None)
        if not isinstance(found, Admin):
            raise _Refused(f"{where} is not an Admin.")
        return found
    for path, module_name in LIKELY:
        if not Path(path).is_file():
            continue
        admins = {
            name: value
            for name, value in vars(_import(module_name)).items()
            if isinstance(value, Admin)
        }
        if len(admins) == 1:
            return next(iter(admins.values()))
        if admins:
            first = next(iter(admins))
            raise _Refused(
                f"{path} holds more than one admin. Say which with --admin, "
                f"such as --admin {module_name}:{first}."
            )
    raise _Refused(
        "no Admin was found in main.py, app.py or app/main.py. Say where it "
        "is with --admin, such as --admin shop.web:admin."
    )


def _import(module_name: str) -> ModuleType:
    """Import one of the project's modules, saying plainly when it is not there."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name and module_name.startswith(error.name):
            raise _Refused(f"there is no module {module_name} here.") from None
        raise


def _password() -> str:
    """The new user's password: from the variable, or typed twice."""
    given = os.environ.get(PASSWORD_VARIABLE)
    if given is not None:
        problem = password_problem(given)
        if problem is not None:
            raise _Refused(f"{PASSWORD_VARIABLE}: {problem}")
        return given
    while True:
        try:
            password = getpass.getpass("Password: ")
            again = getpass.getpass("Password again: ")
        except EOFError:
            raise _Refused(
                f"no password was typed. Type it at a terminal, or give it in "
                f"{PASSWORD_VARIABLE}."
            ) from None
        problem = password_problem(password)
        if problem is not None:
            print(problem, file=sys.stderr)
        elif again != password:
            print("The two passwords differ.", file=sys.stderr)
        else:
            return password


def _sentence(message: str) -> str:
    """A message said after "adminsite:", starting in small letters."""
    return message[:1].lower() + message[1:]
