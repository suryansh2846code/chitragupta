"""An agent asks for anything it needs, and the switches come to the chat.

The measured failure, verbatim: *"Browsing is still not granted — I checked just
now, same block. Settings → Agents & tools → Health & Fitness → let it browse
the web, plus allow changes on `amazon.in`. Say 'go' when it's on."*

Every word of that was true, and the agent had no better option. It was holding
`request_permission`, which could put a tool group on a card and nothing else —
so an ask that needed a group *and* a website could be half a card and half a
paragraph. It wrote the whole thing as a paragraph, which is how a feature built
to stop people being sent to a settings screen sent somebody to a settings
screen.

`agents/access.py` is the one vocabulary of things that can be asked for. These
tests are about three properties of it:

* it understands more than a tool group, and **fails closed** on everything it
  does not understand — every string here was chosen by a model, and some of
  those models are reading a page a stranger wrote;
* a grant **adds**: turning on "click and type" never turns off "read", and a
  tap on one thing is never a tap on a neighbouring thing;
* the panel can only grant what the ask in front of it actually named.
"""
from __future__ import annotations

import pytest

from chitragupta import actions
from chitragupta.agents import access
from chitragupta.agents import tool_facts as tf
from chitragupta.browser import origins


@pytest.fixture(autouse=True)
def no_grants():
    conn = origins._conn()
    conn.execute("DELETE FROM browser_origins")
    conn.commit()
    yield


@pytest.fixture
def agent(monkeypatch):
    """One agent on the roster, holding only what it is created with."""
    from chitragupta.agents.agent import Agent

    made = Agent(id="asker", name="Asker", role="r", system_prompt="",
                 tools=list(tf.granted_by_default()))
    monkeypatch.setattr("chitragupta.agents.list_agents", lambda: [made])
    written: dict = {}

    class _Overrides:
        def set(self, agent_id, tools):
            written[agent_id] = list(tools)
            made.tools = list(tools)       # so `describe` reads back the write

    monkeypatch.setattr("chitragupta.agents.tool_overrides.get_tool_overrides",
                        lambda: _Overrides())
    return made, written


# ── what can be asked for ────────────────────────────────────────────────
@pytest.mark.parametrize("spec, expected", [
    ("websites:read", {"kind": "tool_group", "target": "websites", "level": "read"}),
    ("mac:change", {"kind": "tool_group", "target": "mac", "level": "change"}),
    ("site:amazon.in:change", {"kind": "site", "target": "amazon.in", "level": "change"}),
    # A site with no level is the lesser of the two. Guessing upwards is the one
    # direction this must never guess in.
    ("site:amazon.in", {"kind": "site", "target": "amazon.in", "level": "read"}),
    ("site:https://amazon.in/deals:read",
     {"kind": "site", "target": "amazon.in", "level": "read"}),
])
def test_an_ask_is_read_as_what_it_says(spec, expected):
    assert [n.as_dict() for n in access.parse(spec)] == [expected]


@pytest.mark.parametrize("spec", [
    "websites:run",            # running code is never askable from a card
    "websites:obliterate",
    "on_device:read",          # always on; a switch here would change nothing
    "site:",
    "site:not a host:read",
    "site:amazon.in:destroy",
    "connector:no-such-thing",
    "tool_group:websites",     # a group with no level says nothing
    "nonsense",
    "",
])
def test_an_ask_it_cannot_name_is_dropped(spec):
    """**Unknown fails closed.** The tempting default reads an unrecognised
    thing as the harmless one, and this input was chosen by a model that may
    have been reading a stranger's web page."""
    assert access.parse(spec) == []


def test_one_bad_item_does_not_cost_the_good_ones():
    """A list of three with one typo must not leave the user with nothing — the
    card can only show what it understood, and a short card reads as a small ask
    rather than as a broken one."""
    got = access.parse("websites:read, site:::nonsense, mac:change")

    assert [n.key for n in got] == ["tool_group:websites:read",
                                    "tool_group:mac:change"]


def test_the_same_ask_twice_is_one_switch():
    assert len(access.parse("websites:read, websites:read")) == 1


def test_the_older_pair_still_asks_for_the_same_thing():
    """`group=` + `level=` is the spelling this action shipped with. It is
    folded into the general form rather than handled beside it, so there is one
    representation by the time anything acts on an ask."""
    got = access.needs_from_params({"group": "websites", "level": "read",
                                    "needs": "site:amazon.in:change"})

    assert [n.key for n in got] == ["tool_group:websites:read",
                                    "site:amazon.in:change"]


def test_the_askable_groups_are_derived_not_listed():
    """A hand-kept copy of this drifted once already, in `tool_facts`. Anything
    always-on has nothing to grant and must not appear."""
    assert set(access.askable_groups()) <= {g["key"] for g in tf.permission_groups()}
    assert "on_device" not in access.askable_groups()
    for key in access.askable_groups():
        assert any(tf.tools_for(key, level) for level in tf.ASKABLE), key


def test_the_site_levels_are_the_browsers_own_words():
    """Two spellings of one fact is how a gate and a switch come to disagree
    about what the user allowed."""
    assert set(access.SITE_LEVELS) == {origins.READ, origins.CHANGE}


# ── what the panel shows ─────────────────────────────────────────────────
def test_each_row_is_a_sentence_not_a_pair_of_identifiers(agent):
    rows = access.describe("asker", access.parse(
        "websites:read, site:amazon.in:change"))

    assert [r["title"] for r in rows] == ["Read websites",
                                          "Click and type on amazon.in"]
    assert all(r["means"] for r in rows), rows
    # The pair is the key, never the label — a switch reading "websites: change"
    # asks somebody to consent to two identifiers.
    assert all(r["key"] not in r["title"] for r in rows)


def test_a_row_says_whether_it_is_already_on(agent):
    made, _ = agent
    made.tools = sorted(set(made.tools) | set(tf.tools_for("websites", "read")))

    rows = access.describe("asker", access.parse("websites:read, websites:change"))

    assert [r["granted"] for r in rows] == [True, False]


def test_an_account_is_not_offered_as_a_switch(agent):
    """Google wants a sign-in window, Telegram wants credentials typed, an MCP
    server wants a URL. There is no press that connects all three, and a switch
    that pretended otherwise would be a control that cannot work."""
    rows = access.describe("asker", access.parse("connector:gmail"))

    assert [r["control"] for r in rows] == ["opens"]
    assert rows[0]["screen"] == "connectors"


# ── the grant ────────────────────────────────────────────────────────────
def test_a_tap_grants_exactly_the_one_switch(agent):
    made, written = agent

    access.grant("asker", access.parse("websites:read")[0])

    got = set(written["asker"])
    assert set(tf.tools_for("websites", "read")) <= got
    # Reading was asked for; changing was not.
    assert not set(tf.tools_for("websites", "change")) & got


def test_granting_change_on_a_site_does_not_switch_reading_off(agent):
    """**The bug this comment in `access.grant` is about.** `origins.grant`
    takes both flags and defaults acting to False, so re-granting a site with
    one flag clears the other — which is how a user who allowed clicking would
    find reading turned off by the very press meant to add to it."""
    access.grant("asker", access.parse("site:shop.test:read")[0])
    access.grant("asker", access.parse("site:shop.test:change")[0])

    held = next(g for g in origins.list_grants() if g.host == "shop.test")
    assert held.may_read is True
    assert held.may_act is True


def test_granting_reading_on_a_site_does_not_grant_changing(agent):
    """The two are separate decisions and the panel shows them as two switches.
    One press must never be both."""
    access.grant("asker", access.parse("site:shop.test:read")[0])

    held = next(g for g in origins.list_grants() if g.host == "shop.test")
    assert held.may_read is True
    assert held.may_act is False


def test_granting_something_already_held_writes_nothing(agent):
    made, written = agent
    made.tools = sorted(set(made.tools) | set(tf.tools_for("websites", "read")))

    out = access.grant("asker", access.parse("websites:read")[0])

    assert out["ok"] is True
    assert out["changed"] is False
    assert out["added"] == []
    assert "asker" not in written, "it rewrote a grant that was already there"


def test_an_account_cannot_be_granted_from_the_panel(agent):
    out = access.grant("asker", access.parse("connector:gmail")[0])

    assert out["ok"] is False


# ── the ask, as an action ────────────────────────────────────────────────
def test_the_reason_the_agent_gave_reaches_the_card():
    """**The whole point of the card, and it was being dropped.** The protocol
    has always put the sentence in the tag's body and the card has always asked
    for `params["why"]` — and nothing mapped one to the other, so the row that
    makes the ask answerable rendered empty."""
    parsed = actions.parse_actions(
        '<action type="request_permission" needs="websites:read">'
        'I need to open the page you linked.</action>')

    assert parsed[0]["params"]["why"] == "I need to open the page you linked."


def test_the_older_spelling_is_folded_in_at_the_parse():
    """So one field is the contract downstream, and it is the field the handler
    genuinely refuses without."""
    parsed = actions.parse_actions(
        '<action type="request_permission" group="websites" level="read">'
        'why</action>')

    assert parsed[0]["params"]["needs"] == "websites:read"


def test_confirming_the_whole_ask_grants_every_part_of_it(agent):
    """The panel is the finer control; the queue's Confirm is all-or-nothing,
    because a single button showing three switches cannot mean anything else."""
    out = actions.REGISTRY["request_permission"].handler(
        {"needs": "websites:read, site:shop.test:change", "agent_id": "asker"})

    assert out["ok"] is True
    assert set(out["granted"]) == {"tool_group:websites:read",
                                   "site:shop.test:change"}


def test_an_ask_that_names_nothing_real_is_refused_with_the_list(agent):
    out = actions.REGISTRY["request_permission"].handler(
        {"needs": "websites:obliterate", "agent_id": "asker"})

    assert out["ok"] is False
    assert "site:HOST:read" in out["error"]
    assert "Agents & tools" in out["error"]


# ── the endpoint behind the switches ─────────────────────────────────────
#
# Over HTTP, because the property under test belongs to the route rather than to
# the vocabulary: the panel sends the ask it is showing alongside the key being
# pressed, and the server decides whether that key is one this ask actually
# named. Without that check `/access/grant` is a general "grant anything" route
# — and the agent that raises these asks is sometimes summarising a page a
# stranger wrote.
@pytest.fixture
def client(agent):
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app

    return TestClient(app)


ASK = {"needs": "websites:read, site:shop.test:change", "group": "", "level": ""}


def test_the_panel_is_told_what_is_already_on(client):
    r = client.post("/api/agents/asker/access", json=ASK)

    assert r.status_code == 200
    assert [row["key"] for row in r.json()["rows"]] == [
        "tool_group:websites:read", "site:shop.test:change"]


def test_a_key_this_ask_never_named_is_refused(client):
    """**The reason the whole ask travels with the press.** If the endpoint
    granted any key it was handed, this panel would be a route to every
    permission in the app rather than to the two it is showing."""
    r = client.post("/api/agents/asker/access/grant",
                    json={**ASK, "key": "site:evil.test:change"})

    assert r.status_code == 400
    assert not [g for g in origins.list_grants() if g.host == "evil.test"]


def test_a_key_that_is_not_askable_at_all_is_refused(client):
    """Even named in the ask, running code never becomes a switch."""
    r = client.post("/api/agents/asker/access/grant",
                    json={"needs": "mac:run", "group": "", "level": "",
                          "key": "tool_group:mac:run"})

    assert r.status_code == 400


def test_a_key_the_ask_named_is_granted(client):
    r = client.post("/api/agents/asker/access/grant",
                    json={**ASK, "key": "site:shop.test:change"})

    assert r.status_code == 200
    assert [g.host for g in origins.list_grants()] == ["shop.test"]
    # The reply carries the whole panel again, so each switch repaints from what
    # the server stored rather than from what the client hoped it sent.
    assert [row["granted"] for row in r.json()["rows"]] == [False, True]


# ── asks that a stranger's web page might have written ───────────────────
#
# The model raising these asks is often summarising something it did not write.
# None of the below is hypothetical: each is a shape `browser/origins.py` already
# defends against, and the panel has to inherit that rather than re-derive it.
@pytest.mark.parametrize("spec, shown", [
    # A Cyrillic "а" in amazon. Punycode is what the switch says, so the row
    # reads as the different site it is rather than as the one it imitates.
    ("site:\u0430mazon.in:change", "xn--mazon-3ve.in"),
    # Userinfo before the host: everything left of the @ is a decoy.
    ("site:amazon.com@evil.test:change", "evil.test"),
])
def test_a_lookalike_host_is_shown_as_what_it_really_is(agent, spec, shown):
    rows = access.describe("asker", access.parse(spec))

    assert len(rows) == 1
    assert shown in rows[0]["title"]
    assert "amazon" not in rows[0]["title"].replace(shown, "")


@pytest.mark.parametrize("spec", ["folder:/", "folder:~", "folder:/no/such/dir"])
def test_a_folder_that_could_never_be_granted_is_not_offered(agent, spec):
    """`file_tools` already refuses the whole filesystem and the whole home
    directory. Before this, `folder:/` parsed cleanly and drew a switch whose
    only possible outcome was that refusal — and whose label was "Open ",
    because there is no last path segment to name. A control that cannot work
    reads as the app being broken."""
    assert access.parse(spec) == []


def test_the_folder_rule_has_one_owner(tmp_path):
    """Asked, never copied. A boundary one screen enforces and another describes
    differently is not a boundary."""
    from chitragupta.agents import file_tools

    assert file_tools.folder_problem(str(tmp_path)) == ""
    assert file_tools.folder_problem("/")
    with pytest.raises(ValueError, match=file_tools.folder_problem("/")[:20]):
        file_tools.grant_folder("/")


def test_a_granted_folder_reads_back_as_granted(agent, tmp_path):
    """**Found in a real browser, not in a fixture.** The press wrote, the folder
    was stored, and the switch stayed off for ever — because the need carried
    `/var/...` while `grant_folder` stores the resolved `/private/var/...` that
    macOS's symlink makes of it. One fact, two spellings, and a panel of live
    switches cannot survive saying a grant did not land when it did.

    The symlink is built here rather than borrowed from the platform. The first
    version of this test used `tmp_path`, which on this machine is already
    resolved — so it passed with the bug reintroduced, which is the whole reason
    a regression test has to be watched failing.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    need = access.parse(f"folder:{link}")[0]
    assert need.target == str(real), "the need must carry the stored spelling"
    assert access.describe("asker", [need])[0]["granted"] is False

    assert access.grant("asker", need)["ok"] is True

    assert access.describe("asker", [need])[0]["granted"] is True


# ── reaching a connected app, asked for in the chat ──────────────────────
#
# Whether an agent may use a connected app at all is a permission of its own,
# kept per agent — and the browser is one of those connectors. Every agent had
# the browser's tools switched on and only the one template with
# `unrestricted_connectors` could open a page. The agent could describe the
# problem perfectly and had no way to ask for the thing that would fix it.

def test_reach_is_something_an_agent_can_ask_for():
    from chitragupta.agents import access

    assert "reach" in access.KINDS
    assert [n.key for n in access.parse("reach:browser")] == ["reach:browser"]


def test_reach_is_refused_for_an_app_nothing_is_behind():
    """A grant for a name nothing offers is a switch that turns on and changes
    nothing — the shape of every bug this module exists to stop."""
    from chitragupta.agents import access

    assert access.parse("reach:nonsense") == []


def test_reach_is_not_the_same_ask_as_connecting_an_account():
    """Both are real and they are different: `connector` is *this account is
    not signed in anywhere* and opens its own window; `reach` is *it is signed
    in and I am not allowed to use it* and is one switch."""
    from chitragupta.agents import access

    rows = access.describe("chief-of-staff", access.parse("reach:browser"))
    assert rows[0]["control"] == "switch", (
        "a reach grant is a tap, not a screen to open")


def test_granting_reach_writes_the_per_agent_grant(tmp_path, monkeypatch):
    """The tap has to write the row the gate reads, or the switch turns on and
    the agent is refused anyway — which is the failure it was added for."""
    from chitragupta.agents import access
    from chitragupta.agents.connector_grants import may_use, revoke

    needs = access.parse("reach:browser")
    if not needs:
        pytest.skip("no browser connector on this machine to grant")
    agent = "social-media-manager"
    revoke(agent, "browser")
    assert not may_use(agent, "browser")
    try:
        out = access.grant(agent, needs[0])
        assert out["ok"] and out["changed"], out
        assert may_use(agent, "browser"), "the tap did not reach the gate"
        # Twice is not an error and is not news either.
        again = access.grant(agent, needs[0])
        assert again["ok"] and again["changed"] is False
    finally:
        revoke(agent, "browser")
