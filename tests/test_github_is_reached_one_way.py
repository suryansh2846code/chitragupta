"""GitHub is reached through GitHub's own server, and no other way.

This file used to hold jobs 14 and 15 against a hand-written connector: four
methods, a URL parser, and an allow-list keyed on `acme/api`. That connector
was the last source in the app reachable two ways, and
[`docs/REACHING-AN-APP.md`](../docs/REACHING-AN-APP.md) had already written
down why that is a correctness problem rather than an untidiness — the user
sees the same app twice, the agent picks, and it has no way to pick correctly.

The vendor's own server publishes **45 tools** against the four that were
written here, with the vendor's own schema. So the built-in stood down. It is
still there and still syncs, because somebody has it configured and taking a
source away is not ours to do — what it lost is every way to *write*.

Three judgements from the old file had to survive the move, and each is a test
below rather than a sentence in a commit message:

* **The gate can still see the repository.** `acme/api` was the whole argument
  for the old action being amber instead of red. The grant key now carries it
  (`github:add_issue_comment@acme/api`) and carries the verb too, so it is
  narrower than either the repository key or the bare tool key.
* **An issue still cannot be undone.** GitHub has no delete-issue API, and
  closing one is not the inverse of opening it.
* **A comment can still be taken back** — as a retraction rather than a
  deletion, because GitHub's server publishes no delete-comment tool at all.

The first is held by `test_a_grant_names_what_it_reaches.py` and the last two
by `test_a_connector_write_can_be_taken_back.py`. What is here is the
retirement itself: the things that must be *absent*, which no other file would
notice coming back.
"""
from __future__ import annotations

import pytest

from chitragupta.actions import REGISTRY
from chitragupta.connectors import REGISTRY as CONNECTORS


def test_the_connector_stands_down_for_the_vendors_server():
    assert CONNECTORS["github"].prefer_mcp == "github"


def test_it_still_reads():
    """Retiring a source is not deleting it. Somebody has this configured,
    their brain is full of issues it ingested, and a sync that stopped would
    be the app quietly forgetting what it already knew."""
    assert hasattr(CONNECTORS["github"], "sync")
    assert CONNECTORS["github"].auto_sync is True


@pytest.mark.parametrize("verb", ["comment", "create_issue", "delete_comment",
                                  "issue_exists", "_post", "_refusal"])
def test_it_can_no_longer_write(verb):
    """The half that actually had to go. A retirement that left the write
    methods reachable would be the same two routes with one of them hidden —
    which is worse, because nothing on screen says it is there."""
    assert not hasattr(CONNECTORS["github"], verb)


@pytest.mark.parametrize("gone", ["github_comment", "github_create_issue"])
def test_the_bespoke_actions_are_gone(gone):
    assert gone not in REGISTRY


def test_no_action_anywhere_still_names_the_connector():
    """The check that survives somebody re-adding one by hand. An action whose
    handler reaches `connectors.github` is a second route by definition."""
    from chitragupta import actions

    for name, spec in REGISTRY.items():
        source = getattr(spec.handler, "__code__", None)
        consts = " ".join(str(c) for c in (source.co_consts if source else ()))
        assert "github" not in consts.lower(), (
            f"{name} still reaches the built-in GitHub connector")
    assert not hasattr(actions, "github_target"), (
        "the URL parser outlived the actions that used it")


def test_the_model_is_not_taught_an_action_that_does_not_exist():
    """A prompt block for a removed action is an agent proposing something the
    registry will refuse — which reads to the user as the app being broken."""
    from chitragupta.agents.prompt import _BLOCKS, KNOWN_ACTIONS

    for gone in ("github_comment", "github_create_issue"):
        assert gone not in _BLOCKS
        assert gone not in KNOWN_ACTIONS


def test_an_agent_can_still_act_on_a_repository():
    """The capability must not have gone with the action type. An engineer
    reaches GitHub the way it already reached Linear and Notion — through the
    connector's own write tools, behind a per-tool grant — so what this
    asserts is that it can still *read* one first. An agent that can file an
    issue and cannot search files duplicates."""
    from chitragupta.agents.presets import get_agent

    assert "search_source" in get_agent("engineer").tools


def test_the_researcher_still_acts_on_nothing():
    """It reports; it does not send, schedule or act on the world — and a
    repository write is acting on the world. `mcp_action` is the route now, so
    that is the one it must not hold."""
    from chitragupta.agents.presets import get_agent

    assert "mcp_action" not in set(get_agent("research").actions)
