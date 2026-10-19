"""The adminsite command, run in a project's folder as its owner would run it."""

import getpass
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from adminsite._cli import PASSWORD_VARIABLE, main
from adminsite.users import User

PASSWORD = "a long password"

PROJECT = """
from sqlalchemy import create_engine

from adminsite import Admin
from adminsite.users import AdminUsers

engine = create_engine("sqlite:///shop.db")
admin = Admin(engine, auth={auth}, secret_key="for-the-tests")
"""

WITH_USERS = PROJECT.format(auth="AdminUsers(create_table=True)")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A project's folder, where the command runs, its password given ahead."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv(PASSWORD_VARIABLE, PASSWORD)
    yield tmp_path
    for name in ("main", "app", "app.main", "shop", "shop.web"):
        sys.modules.pop(name, None)


def users_in(folder: Path) -> list[tuple[str, bool]]:
    """Each user the project's database holds, and whether they are a superuser."""
    engine = create_engine(f"sqlite:///{folder / 'shop.db'}")
    with Session(engine) as session:
        found = [
            (user.username, user.is_superuser) for user in session.scalars(select(User))
        ]
    engine.dispose()
    return found


class TestAddingAUser:
    def test_it_adds_the_first_superuser(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / "main.py").write_text(WITH_USERS, encoding="utf-8")

        code = main(["users", "create", "nima", "--superuser"])

        assert code == 0
        assert users_in(project) == [("nima", True)]
        assert capsys.readouterr().out == "Added the superuser nima.\n"

    def test_it_finds_the_admin_it_is_told_about(self, project: Path) -> None:
        (project / "shop").mkdir()
        (project / "shop" / "__init__.py").write_text("", encoding="utf-8")
        (project / "shop" / "web.py").write_text(WITH_USERS, encoding="utf-8")

        code = main(["users", "create", "lena", "--admin", "shop.web:admin"])

        assert code == 0
        assert users_in(project) == [("lena", False)]

    def test_it_looks_in_app_main_too(self, project: Path) -> None:
        (project / "app").mkdir()
        (project / "app" / "__init__.py").write_text("", encoding="utf-8")
        (project / "app" / "main.py").write_text(WITH_USERS, encoding="utf-8")

        assert main(["users", "create", "lena"]) == 0
        assert users_in(project) == [("lena", False)]

    def test_it_asks_for_the_password_twice(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (project / "main.py").write_text(WITH_USERS, encoding="utf-8")
        monkeypatch.delenv(PASSWORD_VARIABLE)
        typed = iter(["short", "short", PASSWORD, "another", PASSWORD, PASSWORD])
        monkeypatch.setattr(getpass, "getpass", lambda prompt: next(typed))

        code = main(["users", "create", "nima", "--superuser"])

        assert code == 0
        said = capsys.readouterr().err
        assert "Enter at least 8 characters." in said
        assert "The two passwords differ." in said
        assert users_in(project) == [("nima", True)]


class TestWhatItRefuses:
    def run(self, capsys: pytest.CaptureFixture[str], *arguments: str) -> str:
        """Run the command, expecting it to refuse, and say what it said."""
        assert main(list(arguments)) == 1
        return capsys.readouterr().err

    def test_a_role_that_does_not_exist(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / "main.py").write_text(WITH_USERS, encoding="utf-8")

        said = self.run(capsys, "users", "create", "lena", "--role", "Support")

        assert said.startswith("adminsite: no role is called 'Support'.")
        assert users_in(project) == []

    def test_a_username_taken_already(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / "main.py").write_text(WITH_USERS, encoding="utf-8")
        main(["users", "create", "nima", "--superuser"])
        capsys.readouterr()

        said = self.run(capsys, "users", "create", "Nima")

        assert "the username 'nima' is taken." in said

    def test_a_short_password_in_the_variable(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (project / "main.py").write_text(WITH_USERS, encoding="utf-8")
        monkeypatch.setenv(PASSWORD_VARIABLE, "short")

        said = self.run(capsys, "users", "create", "nima")

        assert f"{PASSWORD_VARIABLE}: Enter at least 8 characters." in said

    def test_an_admin_whose_users_are_its_own(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sign_in = 'PasswordAuth({"nima": hash_password("letmein")})'
        code = PROJECT.format(auth=sign_in).replace(
            "from adminsite.users import AdminUsers",
            "from adminsite.auth import PasswordAuth, hash_password",
        )
        (project / "main.py").write_text(code, encoding="utf-8")

        said = self.run(capsys, "users", "create", "nima")

        assert "does not keep its users with AdminUsers" in said

    def test_a_folder_without_an_admin(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        said = self.run(capsys, "users", "create", "nima")

        assert "no Admin was found in main.py, app.py or app/main.py" in said

    def test_tables_that_are_not_there(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = PROJECT.format(auth="AdminUsers()")
        (project / "main.py").write_text(code, encoding="utf-8")

        said = self.run(capsys, "users", "create", "nima")

        assert "the users' tables are not in the database yet" in said

    def test_an_admin_named_wrong(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / "main.py").write_text(WITH_USERS, encoding="utf-8")

        said = self.run(capsys, "users", "create", "nima", "--admin", "main:engine")

        assert "main:engine is not an Admin." in said
