"""The app the Getting started page builds runs as the page says."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

PAGE = Path(__file__).parents[1] / "docs" / "getting-started.md"


def save_and_import(directory: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The page's first example, saved as main.py as the page asks, and imported."""
    found = re.search(r"```python\n(.*?)\n```", PAGE.read_text("utf-8"), re.DOTALL)
    assert found is not None
    path = directory / "main.py"
    path.write_text(found.group(1), "utf-8")
    spec = importlib.util.spec_from_file_location("main", path)
    assert spec is not None and spec.loader is not None
    main = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "main", main)
    spec.loader.exec_module(main)
    return main


async def test_the_first_admin_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The example keeps its shop.db in the folder it runs from.
    monkeypatch.chdir(tmp_path)
    main = save_and_import(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=main.app)
    try:
        async with (
            main.app.router.lifespan_context(main.app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            orders = await client.get("/admin/orders")
            searched = await client.get("/admin/orders", params={"q": "Omar"})
            customers = await client.get("/admin/customers")
    finally:
        await main.engine.dispose()

    assert orders.status_code == 200
    assert "Lena Fischer" in orders.text
    assert "Omar Haddad" in searched.text
    assert "Lena Fischer" not in searched.text
    assert "lena@example.com" in customers.text
