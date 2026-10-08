"""An error a user reads is a sentence, never an internal.

`api()` rejects with `e.detail || r.statusText`, and the frontend used to put
that value on screen with `String(e)` in ~45 places. Most of the time `detail`
is a sentence written for a person and that was fine. Three shapes were not:

* the body was not JSON — a 502 or a crash page — so `r.json()` threw and the
  toast read *"SyntaxError: Unexpected token '<'…"*;
* FastAPI validation failed, so `detail` was a **list of objects** and
  `String([...])` produced the literal text `[object Object]`;
* the server was gone, giving a bare `TypeError: Failed to fetch`.

`/CLAUDE.md` — *"Never surface an internal. No raw provider JSON, no stack
traces, no internal ids in user-facing text."* One translator in `core.js`
rather than 45 fixes, so the next call site inherits it.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


@pytest.fixture(scope="module")
def out() -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/error_text.mjs"), str(WEB / "app.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


# ── the three shapes that were reaching the screen ─────────────────────────

def test_a_non_json_reply_does_not_say_syntaxerror(out):
    said = out["notJson"]
    assert "SyntaxError" not in said and "token" not in said, said
    assert said.endswith("."), f"{said!r} is not a sentence"


def test_the_server_being_gone_says_so(out):
    assert "Failed to fetch" not in out["serverGone"]
    assert "Chitragupta" in out["serverGone"], out["serverGone"]


def test_a_validation_error_is_never_object_Object(out):
    """`detail` is a list of per-field objects. The one readable part is `msg`."""
    assert out["validation"] == "field required", out["validation"]


def test_no_shape_can_produce_object_Object(out):
    for name, said in out.items():
        assert "[object Object]" not in said, f"{name} rendered {said!r}"


# ── and the cases that must keep working ───────────────────────────────────

def test_a_detail_written_for_a_person_is_passed_through(out):
    """The common case, and the reason this is a translator and not a blanket
    'Something went wrong' — the server's own wording is usually the best one."""
    assert out["plainDetail"] == "That folder is outside your home directory."
    assert out["objectWithDetail"] == "The key was rejected."
    assert out["ordinary"] == "Ollama is not running."


@pytest.mark.parametrize("key", ["nullish", "undef", "emptyString", "bareObject"])
def test_nothing_useful_still_says_something(out, key):
    assert out[key] == "Something went wrong. Please try again."


def test_a_caller_can_say_what_failed(out):
    assert out["custom"] == "Could not load your agents."


# ── the shape: no call site may go back to printing the raw value ──────────

def test_no_screen_prints_a_raw_rejection_value():
    """The whole point of one translator is that nobody re-adds the 45th
    `String(e)`. `core.js` is skipped — it defines the translator."""
    offenders = []
    pattern = re.compile(r"String\((?:e|err|exc|ex)\)|(?:e|err|exc|ex)\.message \|\| (?:e|err|exc|ex)")
    for js in sorted(WEB.glob("*.js")):
        if js.name in {"character.js", "core.js"}:
            continue
        for i, line in enumerate(js.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{js.name}:{i}: {line.strip()[:90]}")
    assert not offenders, (
        "these put a raw rejection value on screen — use errText(e):\n"
        + "\n".join(offenders))
