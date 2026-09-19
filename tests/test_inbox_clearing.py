"""“Clear the emails that don't need my attention” — the whole job.

Every part of this existed separately: `list_mail` for the ids, one
`mail_triage` for the batch, `create_draft` for the replies, `<plan>` to put
them under one button. Four capabilities is not the same as the one job people
actually ask for, so the assembly is written down in `prompt._INBOX_RECIPE` and
scored in `evaluation.py`.

What this pins:

* **The recipe reaches an agent that can do all of it, and nobody else.** A
  recipe for a capability an agent half-has is worse than none: an agent told
  to draft replies with no `create_draft` will describe drafts it did not
  write.
* **The four rules are all there**, because each is a way the job goes wrong
  late — a guessed id, an archived thread somebody was waiting on, a silent
  decision about what was left, or seventeen cards instead of one.
* **A message being replied to is never also archived.** This is the failure
  the user cannot see: an email quietly archived under a draft is one they
  will not know to look for.
* **The card reads as a decision, not as a count.** "9 inbox changes" on the
  one card meant to make the whole job legible is the version that fails.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chitragupta.actions import parse_plans
from chitragupta.agents.prompt import _INBOX_RECIPE, _inbox_recipe, build

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

#: What the model is meant to produce for the headline request.
CLEARED = """Four emails. Two are noise, one is an FYI, and Rahul is waiting.
I left Rahul's in your inbox as well, so you can see the thread.
<plan rationale="4 emails — 2 newsletters, 1 to mark read, 1 needs you">
<action type="mail_triage">{"items":[
 {"id":"m1","do":"archive","subject":"Flash sale"},
 {"id":"m2","do":"archive","subject":"Weekly newsletter"},
 {"id":"m3","do":"mark_read","subject":"Build passed"}]}</action>
<action type="create_draft" to="rahul@work.test" subject="Re: Proposal?" \
thread_id="t4">Sending it tonight.</action>
</plan>"""


# ── who is taught the job ──────────────────────────────────────────────────

def test_an_agent_with_every_piece_gets_the_recipe():
    assert _inbox_recipe(["list_mail"], ["mail_triage", "create_draft"])


@pytest.mark.parametrize("tools,actions", [
    ([], ["mail_triage", "create_draft"]),                  # cannot read mail
    (["list_mail"], ["create_draft"]),                      # cannot batch
    (["list_mail"], ["mail_triage"]),                       # cannot draft
    (["list_mail"], []),                                    # can only read
])
def test_an_agent_missing_a_piece_is_not_taught_the_job(tools, actions):
    """An agent told to draft replies with no `create_draft` will describe
    drafts it did not write, and one told to batch with no `mail_triage`
    proposes a card it cannot fill."""
    assert _inbox_recipe(tools, actions) == ""


def test_the_shipped_inbox_agent_is_taught_it():
    from chitragupta.agents.presets import get_agent

    agent = get_agent("inbox")
    prompt = build(name=agent.name, role=agent.role,
                   system_prompt=agent.system_prompt, tools=agent.tools,
                   actions=agent.actions, agent_id=agent.id)
    assert "CLEARING THE INBOX" in prompt


def test_an_agent_with_no_inbox_is_not():
    from chitragupta.agents.presets import get_agent

    agent = get_agent("research")
    prompt = build(name=agent.name, role=agent.role,
                   system_prompt=agent.system_prompt, tools=agent.tools,
                   actions=agent.actions, agent_id=agent.id)
    assert "CLEARING THE INBOX" not in prompt


def test_the_recipe_comes_after_the_planning_block():
    """It is an instance of planning — it says what to put IN a plan, and reads
    as nonsense to an agent that has not been told plans exist."""
    from chitragupta.agents.presets import get_agent

    agent = get_agent("inbox")
    prompt = build(name=agent.name, role=agent.role,
                   system_prompt=agent.system_prompt, tools=agent.tools,
                   actions=agent.actions, agent_id=agent.id)
    assert prompt.index("SEVERAL ACTIONS AT ONCE") < prompt.index("CLEARING THE INBOX")


# ── what the recipe actually says ──────────────────────────────────────────

def test_it_forbids_guessing_an_id():
    """A guessed id archives somebody else's message and nobody finds out."""
    assert "list_mail" in _INBOX_RECIPE
    assert "guessed id" in _INBOX_RECIPE


def test_it_names_all_four_piles():
    for pile in ("archive", "mark_read", "draft", "leave it alone"):
        assert pile in _INBOX_RECIPE, pile


def test_it_forbids_archiving_something_it_is_also_replying_to():
    assert "NEVER archive or mark-read a message you are also drafting" in _INBOX_RECIPE


def test_it_asks_for_one_plan_and_a_rationale_of_counts():
    assert "ONE plan" in _INBOX_RECIPE
    assert "rationale" in _INBOX_RECIPE


def test_it_asks_the_agent_to_say_what_it_left_behind():
    """An email you quietly archived is one they will not know to look for."""
    assert "left for them" in _INBOX_RECIPE


# ── the shape that comes back ──────────────────────────────────────────────

def test_the_job_parses_as_one_plan_of_two_steps():
    (plan,) = parse_plans(CLEARED)
    assert [s["type"] for s in plan.steps] == ["mail_triage", "create_draft"]
    assert "4 emails" in plan.rationale


def test_the_message_being_replied_to_is_not_in_the_archive_batch():
    """The way this job fails silently."""
    (plan,) = parse_plans(CLEARED)
    triaged = {i["id"] for s in plan.steps if s["type"] == "mail_triage"
               for i in s["params"]["items"]}
    assert triaged == {"m1", "m2", "m3"}
    assert "m4" not in triaged


def test_the_whole_batch_is_still_red():
    """Fourteen green archives do not make an inbox change something an
    unattended agent may do: its input is text strangers wrote."""
    (plan,) = parse_plans(CLEARED)
    assert plan.risk().value == "red"


def test_the_prose_survives_so_the_user_reads_what_was_left():
    from chitragupta.actions import strip_plans

    left = strip_plans(CLEARED)
    assert "left Rahul's in your inbox" in left


# ── the card ───────────────────────────────────────────────────────────────

CATALOG = {
    "mail_triage": {"label": "Inbox changes", "fields": ["items"], "risk": "red",
                    "reversible": True, "undo_label": "Put them back",
                    "always_ask_because":
                        "Changing your inbox always needs your approval."},
    "create_draft": {"label": "Save a draft",
                     "fields": ["to", "cc", "subject", "body", "attach"],
                     "risk": "green", "reversible": True,
                     "undo_label": "Discard it", "always_ask_because": ""},
}


def _card(plan, result=None):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps({"plan": plan, "catalog": CATALOG,
                          "result": result or {"ok": True, "detail": "All 2 done.",
                                               "steps": [], "skipped": [],
                                               "undoable": []}}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def _plan_from(reply):
    (plan,) = parse_plans(reply)
    return {"rationale": plan.rationale, "steps": plan.steps}


def test_the_card_leads_with_what_the_agent_understood():
    out = _card(_plan_from(CLEARED))
    assert "4 emails — 2 newsletters, 1 to mark read, 1 needs you" in out["text"]


def test_a_triage_step_reads_as_a_decision_not_a_count():
    """"3 inbox changes" on the one card meant to make this job legible is the
    version that fails."""
    out = _card(_plan_from(CLEARED))
    assert "Archive 2 emails" in out["text"]
    assert "Mark read 1 email" in out["text"]
    assert "inbox changes" not in out["text"].lower()


def test_the_draft_is_named_on_the_same_card():
    out = _card(_plan_from(CLEARED))
    assert "rahul@work.test" in out["text"]
    assert "Re: Proposal?" in out["text"]


def test_the_card_carries_the_inbox_reason_because_one_step_is_red():
    out = _card(_plan_from(CLEARED))
    assert out["risk"] == "red"
    assert "Changing your inbox always needs your approval." in out["text"]


def test_one_button_for_the_whole_job():
    out = _card(_plan_from(CLEARED))
    assert out["text"].count("Approve") == 1
    assert out["sent"]["steps"][0]["type"] == "mail_triage"
    assert out["sent"]["steps"][1]["type"] == "create_draft"


def test_a_short_plan_does_not_repeat_itself_in_counts():
    """The grouped counts only earn their space when the step list below is
    truncated — and "Inbox changes once" over a step that is three emails is
    repeating it badly."""
    out = _card(_plan_from(CLEARED))
    assert "once" not in out["text"]


# ── after it runs ──────────────────────────────────────────────────────────

DONE = {
    "ok": True, "detail": "All 2 done.", "skipped": [],
    "undoable": ["L2", "L1"],
    "steps": [
        {"index": 0, "type": "mail_triage",
         "summary": "Archive 2 emails, Mark read 1 email",
         "result": {"ok": True, "log_id": "L1", "reversible": True}},
        {"index": 1, "type": "create_draft",
         "summary": "Draft “Re: Proposal?” to rahul@work.test",
         "result": {"ok": True, "log_id": "L2", "reversible": True}}]}


def test_the_whole_job_can_be_taken_back():
    """Archive removes INBOX so undo adds it back, and a draft is discarded.
    Every step of this job is reversible, which is what makes one tap over
    fourteen emails a reasonable thing to ask for."""
    out = _card(_plan_from(CLEARED), DONE)
    assert out["hasUndo"]
    assert out["undoLabel"] == "Undo 2 of them"


def test_each_step_reports_its_own_outcome():
    out = _card(_plan_from(CLEARED), DONE)
    assert "Archive 2 emails" in out["afterConfirm"]
    assert "rahul@work.test" in out["afterConfirm"]


# ── the scorecard ──────────────────────────────────────────────────────────

def test_the_job_is_on_the_scorecard():
    """`agents/CLAUDE.md`: every new capability gets a case in evaluation.py."""
    from chitragupta.agents import evaluation

    card = evaluation.run(include_slow=False)
    keys = {c.key for c in card.checks}
    assert {"inbox_clearing", "inbox_clearing_drafts",
            "inbox_clearing_gate"} <= keys
    assert all(c.passed for c in card.checks if c.key.startswith("inbox_clearing")), (
        [c.detail for c in card.checks if c.key.startswith("inbox_clearing")])
