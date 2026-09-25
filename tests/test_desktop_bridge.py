"""The HTTP layer asks the native window for things without importing it.

`docs/ARCHITECTURE.md` §3 rule 5 — `desktop.py` and `hud.py` sit beside `api/`,
and `api/` must not import them. Three routes did, and `desktop.py` imports
`api.app` to serve the window, so the two were a cycle. It was invisible because
every one of those imports sat inside a function body, where `ruff` cannot see
it and where it reads as entirely reasonable.

Two things have to hold, and the second is the one that rots quietly:

* **detached must be honest** — `chitragupta serve` is a browser tab with no
  native window, and the right answer to "is the floating card available" is
  *no*, in the same shape the attached surface uses;
* **attached must actually reach the window** — a bridge that is never wired up
  behaves exactly like one that is, for every existing test, because the
  fallbacks are indistinguishable from the real values.
"""
from __future__ import annotations

import pytest

from chitragupta import hud
from chitragupta.api import desktop_bridge


@pytest.fixture(autouse=True)
def _detached():
    """Every test starts with no window, and leaves none behind."""
    desktop_bridge.detach()
    yield
    desktop_bridge.detach()


# ── the arrow points the right way ─────────────────────────────────────────

def test_no_route_imports_the_window_layer():
    """The rule this whole module exists to keep. A function-level import is
    still an import — it is only harder to see, which is why this walks the AST
    rather than grepping: `from .. import desktop_bridge` contains the string
    `import desktop`, and a substring check calls the fix a violation."""
    import ast
    import pathlib

    api = pathlib.Path(desktop_bridge.__file__).parent
    offenders = []
    for path in sorted(api.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                if name.split(".")[-1] in ("hud", "desktop"):
                    offenders.append(f"{path.name}:{node.lineno} -> {name}")
    assert not offenders, offenders


def test_the_bridge_does_not_import_the_window_layer_either():
    """Otherwise the seam is a re-export and the cycle is back."""
    import pathlib
    src = pathlib.Path(desktop_bridge.__file__).read_text()
    assert "import hud" not in src
    assert "from ... import" not in src


# ── detached: the browser-tab case ─────────────────────────────────────────

def test_detached_reports_no_window_rather_than_failing():
    assert desktop_bridge.attached() is False
    assert desktop_bridge.diagnostics()["available"] is False


def test_detached_diagnostics_keep_the_same_shape():
    """A status endpoint that changes shape depending on how the app was
    launched is one the frontend has to branch on."""
    detached = set(desktop_bridge.diagnostics())
    desktop_bridge.attach(hud)
    assert set(desktop_bridge.diagnostics()) == detached


def test_a_note_with_no_window_is_a_no_op_not_an_error():
    """These notes exist to explain why the card did or did not appear. In a
    browser tab there is no card for them to be about."""
    desktop_bridge.note("branch", provider="claude", branch="skipped")


def test_detached_still_gives_the_page_a_timeout():
    """A missing field is worse than a default: the page uses it to count down."""
    assert desktop_bridge.signin_timeout_seconds() == \
        desktop_bridge.DEFAULT_SIGNIN_TIMEOUT_SECONDS


# ── attached: the thing every other test cannot see ────────────────────────

def test_attaching_routes_a_note_to_the_window_layer():
    """The gap this file was written for. The fallbacks are indistinguishable
    from the real values, so a bridge nobody wired up passes every existing
    sign-in test."""
    seen: list[tuple] = []
    desktop_bridge.attach(type("S", (), {
        "SIGNIN_TIMEOUT_SECONDS": 42,
        "note": staticmethod(lambda event, **f: seen.append((event, f))),
        "diagnostics": staticmethod(lambda: {"available": True}),
    })())
    desktop_bridge.note("branch", provider="claude")
    assert seen == [("branch", {"provider": "claude"})]


def test_attaching_takes_the_windows_own_timeout_not_the_default():
    desktop_bridge.attach(type("S", (), {
        "SIGNIN_TIMEOUT_SECONDS": 42,
        "note": staticmethod(lambda *a, **k: None),
        "diagnostics": staticmethod(dict),
    })())
    assert desktop_bridge.signin_timeout_seconds() == 42


def test_attaching_takes_the_windows_own_diagnostics():
    desktop_bridge.attach(type("S", (), {
        "SIGNIN_TIMEOUT_SECONDS": 1,
        "note": staticmethod(lambda *a, **k: None),
        "diagnostics": staticmethod(lambda: {"available": True, "trail": ["x"]}),
    })())
    assert desktop_bridge.diagnostics()["trail"] == ["x"]


def test_hud_satisfies_the_surface_it_is_attached_as():
    """`_attach_to_api` hands the module itself over, so the module has to have
    the shape. A missing attribute here is an AttributeError at sign-in time."""
    hud._attach_to_api()
    assert desktop_bridge.attached()
    assert isinstance(desktop_bridge.signin_timeout_seconds(), int)
    assert "available" in desktop_bridge.diagnostics()
    desktop_bridge.note("branch", provider="test", branch="unit")


def test_the_desktop_entry_point_attaches():
    """Wiring that exists and is never called is wiring that does not exist."""
    import inspect
    import pathlib
    src = pathlib.Path(inspect.getfile(hud)).parent / "desktop.py"
    assert "_attach_to_api()" in src.read_text()


# ── the endpoint still answers ─────────────────────────────────────────────

def test_the_diagnostics_route_works_with_no_window():
    """The endpoint is what support reads. It must answer in a browser tab."""
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app
    body = TestClient(app).get("/api/hud/diagnostics").json()
    assert body["available"] is False
    assert "login_processes" in body
