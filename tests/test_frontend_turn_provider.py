"""A turn names no provider, because the agent's own binding decides.

**This file used to assert the opposite**, and the history is the reason it now
asserts what it does — it is the same bug twice, and a third version has to not
be the third occurrence.

*First version.* The turn read `$("#provider").value`, a hidden `<select>`
empty until `loadProviders()` fills it — roughly ten seconds cold. For those
ten seconds the request carried nothing, the server fell back to
`settings.model_provider` (`mock` on a fresh install), and the offline model
answered dressed as a real one. Two replies came back as a truncated echo of
the recall block, which is `MockProvider`'s signature.

*Second version.* Read localStorage instead, which the picker writes
synchronously. That made the CLIENT authoritative — and `run_turn` treats a
provider on the request as an **override** beating `agent.model_provider`. So
when the stored value went stale (`chitragupta_provider` left on `"mock"`), it
beat a perfectly good agent binding on every turn, and the same symptom came
back with the causes reversed: the chip said "Claude Opus 5" and the mock
replied.

Both failed the same way: **the value on screen and the value in the request
came from different places.** There is one place now — the agent binding, which
the picker POSTs to `/api/agents/{id}/model`, both labels render, and
`run_turn` resolves when the request stays quiet. So the request names no
provider at all, and an agent with no binding falls to
`settings.model_provider`, which is exactly what the "Auto" label means.

The tests below were left asserting version two after `chat.js` shipped version
three, so all three failed on a contract that had been deliberately removed.
They pin the new contract now, including the race that started all of this.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


def _turn(saved, select_value="") -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/turn_provider.mjs"), str(WEB / "app.js")],
        input=json.dumps({"saved": saved, "selectValue": select_value}),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_the_turn_still_goes_out():
    """Before anything about its contents: a request is actually made. Without
    this, every assertion below would pass on a turn that never happened."""
    out = _turn("openai", select_value="")

    assert any("/chat" in p for p in out["allPaths"]), out["allPaths"]


def test_the_request_names_no_provider():
    """The whole contract. A provider on the request is an OVERRIDE in
    `run_turn`, so sending one — from anywhere — means the client can beat the
    agent's binding and the screen stops matching the answer."""
    assert _turn("openai", select_value="")["sentProvider"] is None


def test_a_stale_localStorage_copy_cannot_beat_the_binding():
    """Version two's failure, pinned. `chitragupta_provider` had been left on
    "mock" and won every turn while the chip rendered the real model."""
    assert _turn("mock", select_value="")["sentProvider"] is None


def test_the_hidden_select_cannot_beat_it_either():
    """Version one's failure, pinned from the other side."""
    assert _turn(None, select_value="ollama")["sentProvider"] is None


def test_neither_source_disagreeing_changes_anything():
    """Both set and contradicting each other used to decide the answer. Now
    there is nothing for them to decide."""
    assert _turn("anthropic", select_value="openai")["sentProvider"] is None
