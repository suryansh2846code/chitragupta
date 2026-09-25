"""What an agent may change is listed fairly, and truncation is said out loud.

Reported from a transcript. An agent told the user *"my GitHub access here is
read-only, and the only GitHub actions I can propose are commenting on an issue
or a pending PR review"*. That was an honest account of what it had been shown
and completely wrong about GitHub, whose server publishes eighteen write tools
including `create_or_update_file`, `create_pull_request` and `push_files`.

The prompt took `write_tools()[:20]`. Notion sorts before GitHub and publishes
eighteen writes of its own, so it spent the budget and GitHub got the two
slots left. The other sixteen were not merely unlisted — the block then tells
the model *"use ONLY the arguments listed"*, so a tool it cannot see is a tool
that does not exist, and the agent reports the capability as missing.

Two properties come out of it, and the second is the one that matters when the
budget really does run out:

* **Every connector gets a share.** Round-robin, so the last one added is as
  visible as the first, whatever it happens to be called.
* **What is dropped is named.** A list quietly cut reads as the whole truth.
  Saying "and 10 more on Notion" turns "it cannot" into "I cannot see it",
  which is the difference between a wrong answer and a careful one.
"""
from __future__ import annotations

from collections import Counter

from chitragupta.agents.prompt import MAX_WRITE_TOOLS_SHOWN, _fair_share


class _Ref:
    def __init__(self, server_id: str, tool: str) -> None:
        self.server_id, self.tool = server_id, tool
        self.server_label = server_id.title()


def refs(**counts: int) -> list[_Ref]:
    return [_Ref(sid, f"{sid}_t{i}") for sid, n in counts.items()
            for i in range(n)]


# ── the reported case ─────────────────────────────────────────────────────


def test_one_chatty_connector_no_longer_hides_another():
    """The transcript. Eighteen Notion writes must not cost GitHub sixteen."""
    shown, _ = _fair_share(refs(notion=18, github=18), MAX_WRITE_TOOLS_SHOWN)

    by_server = Counter(r.server_id for r in shown)
    assert by_server["github"] == 18, "GitHub's writes were crowded out again"
    assert by_server["notion"] == 18


def test_the_old_flat_cap_is_what_did_it():
    """Pinned so the regression is recognisable rather than mysterious: taking
    the first twenty of that same list leaves GitHub with two."""
    flat = refs(notion=18, github=18)[:20]

    assert Counter(r.server_id for r in flat)["github"] == 2


# ── when the budget genuinely runs out ────────────────────────────────────


def test_the_budget_is_split_evenly():
    shown, _ = _fair_share(refs(a=18, b=18, c=18, d=18, e=18), 40)

    counts = set(Counter(r.server_id for r in shown).values())
    assert counts == {8}, counts
    assert len(shown) == 40


def test_what_was_dropped_is_reported_per_connector():
    """A caller that cannot say what it dropped will silently claim it dropped
    nothing — which is exactly how this shipped."""
    _, left = _fair_share(refs(a=18, b=18, c=18, d=18, e=18), 40)

    assert {sid: n for sid, (_, n) in left.items()} == {
        "a": 10, "b": 10, "c": 10, "d": 10, "e": 10}


def test_the_label_comes_with_the_count():
    """The sentence a model reads names the connector, not its id."""
    _, left = _fair_share(refs(notion=30), 5)

    assert left["notion"] == ("Notion", 25)


def test_nothing_is_reported_when_nothing_was_dropped():
    shown, left = _fair_share(refs(a=3, b=4), 40)

    assert len(shown) == 7
    assert left == {}


def test_a_small_connector_is_never_starved_by_a_large_one():
    """Round-robin, not proportional: a connector with three tools shows all
    three even beside one with fifty."""
    shown, left = _fair_share(refs(big=50, small=3), 20)

    assert Counter(r.server_id for r in shown)["small"] == 3
    assert "small" not in left


def test_no_connectors_is_not_an_error():
    assert _fair_share([], 40) == ([], {})


# ── the call site, which is where the bug actually was ────────────────────


def _block(monkeypatch, tools: list) -> str:
    """The real CONNECTOR ACTIONS block, with the servers under our control."""
    import chitragupta.connectors.mcp_tools as connector_tools
    from chitragupta.agents import prompt
    from chitragupta.agents.mcp_tools import SENTINEL

    monkeypatch.setattr(connector_tools, "write_tools", lambda: tools)
    return prompt._connector_actions([SENTINEL])


def test_the_prompt_itself_shares_fairly(monkeypatch):
    """Testing `_fair_share` alone proved nothing about the bug: it lived at
    the call site, which took `write_tools()[:20]` and never asked."""
    block = _block(monkeypatch, refs(notion=18, github=18))

    for i in range(18):
        assert f'tool="github_t{i}"' in block, f"github_t{i} never reached the model"


def test_the_prompt_says_what_it_left_out(monkeypatch):
    block = _block(monkeypatch, refs(notion=30, github=30))

    assert "more on Notion" in block and "more on Github" in block
    # And tells the model what to DO about it, which is the whole point: the
    # failure was an agent reporting a capability as absent.
    assert "never that it cannot" in block


def test_a_complete_list_makes_no_excuses(monkeypatch):
    """A truncation notice on a list that was not truncated would teach the
    model to hedge about connectors it can see perfectly well."""
    block = _block(monkeypatch, refs(notion=2, github=2))

    assert "more on" not in block
