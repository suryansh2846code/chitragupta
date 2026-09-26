"""The WHEN / ONLY IF / THEN builder, executed.

Two claims, and neither can be read off the source.

* **What the form sends is the schema the engine already reads.** A flat list of
  conditions, a number where a number is meant, a list where a list is meant,
  and a question where the condition takes a prompt. A form that invented its
  own shape would be a second description of the automation schema, and the two
  would disagree within a release.
* **The options came from the server.** The vocabulary endpoint reads the
  trigger and condition registries, so a condition added in Python appears in
  the form. The test proves it by inventing one the frontend has never heard of
  and finding it in the rendered select.

The vocabulary below is not hand-written: it is the real endpoint's answer,
taken from the app in the same test session, so a change to the registry that
breaks the form fails here rather than in a browser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "chitragupta" / "web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


@pytest.fixture(scope="module")
def vocabulary() -> dict:
    """The real published vocabulary, so the form is tested against the engine."""
    from chitragupta.api.app import app

    with TestClient(app) as client:
        return client.get("/api/automations/vocabulary").json()


def drive(vocabulary, script, automation=None, fail_patch=False) -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/automation_builder.mjs"), str(WEB / "app.js")],
        input=json.dumps({"vocabulary": vocabulary, "script": script,
                          "automation": automation or {},
                          "failPatch": fail_patch}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── the options are the engine's, not a copy ────────────────────────────────

def test_the_trigger_choices_come_from_the_engine(vocabulary):
    out = drive(vocabulary, [{"op": "open"}])
    for spec in vocabulary["triggers"]:
        assert f'value="{spec["type"]}"' in out["triggerHtml"]
        assert spec["label"] in out["triggerHtml"]


def test_a_condition_the_frontend_has_never_heard_of_still_appears(vocabulary):
    """The drift test. A condition registered in Python reaches the form without
    a frontend change, which is the only reason the two can stay in step."""
    invented = dict(vocabulary)
    invented["conditions"] = [*vocabulary["conditions"],
                              {"type": "smells_wrong", "label": "smells wrong",
                               "field": True, "value": "text"}]
    out = drive(invented, [{"op": "open"}, {"op": "add"}])
    assert "smells wrong" in out["conditionsHtml"]
    assert 'value="smells_wrong"' in out["conditionsHtml"]


def test_a_group_is_not_offered_as_a_row(vocabulary):
    """`all` / `any` / `not` are real and this form cannot express them. Offering
    one as a row would make a condition that holds other conditions look like a
    condition about a field."""
    out = drive(vocabulary, [{"op": "open"}, {"op": "add"}])
    assert "all of these" not in out["conditionsHtml"]


def test_the_field_names_offered_are_the_ones_events_carry(vocabulary):
    """And they are offered by name, not typed.

    The first version of this form had a text box you typed `event.from` into,
    with the paths as autocomplete hints. That is a dotted path in front of
    somebody who wants "who it is from", and a typo in it is a condition that
    silently never matches.
    """
    out = drive(vocabulary, [{"op": "open"}, {"op": "add"}])

    assert 'value="event.from"' in out["conditionsHtml"]
    assert "Who it is from" in out["conditionsHtml"], (
        "the path is shown where the name should be")
    assert 'value="event.subject"' not in out["conditionsHtml"], (
        "an event does not carry `subject` — offering it is offering a "
        "condition that can never be true")
    assert "cond-field" in out["conditionsHtml"]
    assert 'list="rmFieldList"' not in out["conditionsHtml"], (
        "it is still a typed box")


def test_a_field_the_automation_uses_is_kept_even_if_it_is_not_offered(
        vocabulary):
    """A dropdown that silently swaps an unrecognised value for its first item
    rewrites the user's automation the moment they open it to look."""
    existing = {"id": "a3", "trigger": {"type": "manual"},
                "conditions": [{"type": "contains", "field": "event.custom",
                                "value": "x"}]}
    out = drive(vocabulary, [{"op": "open", "existing": {"id": "a3"}}],
                automation=existing)

    assert 'value="event.custom"' in out["conditionsHtml"]
    assert out["conditions"] == [
        {"type": "contains", "field": "event.custom", "value": "x"}]


# ── what it sends ──────────────────────────────────────────────────────────

def test_two_conditions_become_the_flat_list_the_engine_evaluates(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"},
        {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "domain_is"},
        {"op": "type", "row": 0, "what": "field", "value": "event.from"},
        {"op": "type", "row": 0, "what": "value", "value": "acme.com"},
        {"op": "add"},
        {"op": "type", "row": 1, "what": "type", "value": "contains"},
        {"op": "type", "row": 1, "what": "field", "value": "event.title"},
        {"op": "type", "row": 1, "what": "value", "value": "invoice"},
    ])
    assert out["conditions"] == [
        {"type": "domain_is", "field": "event.from", "value": "acme.com"},
        {"type": "contains", "field": "event.title", "value": "invoice"},
    ]


def test_a_number_is_sent_as_a_number(vocabulary):
    """`older_than_days` does `float(spec["value"])`. A string works today and
    is one refactor from not working, and the registry already says which
    conditions take a number."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "older_than_days"},
        {"op": "type", "row": 0, "what": "field", "value": "event.uri"},
        {"op": "type", "row": 0, "what": "value", "value": "7"},
    ])
    assert out["conditions"] == [
        {"type": "older_than_days", "field": "event.uri", "value": 7}]


def test_a_list_condition_is_sent_as_a_list(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "in"},
        {"op": "type", "row": 0, "what": "field", "value": "event.state"},
        {"op": "type", "row": 0, "what": "value", "value": "open, closed , "},
    ])
    assert out["conditions"] == [
        {"type": "in", "field": "event.state", "value": ["open", "closed"]}]


def test_a_condition_about_the_field_alone_sends_no_value(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "exists"},
        {"op": "type", "row": 0, "what": "field", "value": "event.from"},
    ])
    assert out["conditions"] == [{"type": "exists", "field": "event.from"}]
    assert "cond-value" not in out["conditionsHtml"], (
        "it asked for a value it will not send")


def test_a_model_judgement_is_sent_as_a_question(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "semantic"},
        {"op": "type", "row": 0, "what": "value", "value": "is this from a client?"},
    ])
    assert out["conditions"] == [
        {"type": "semantic", "question": "is this from a client?"}]
    assert "cond-field" not in out["conditionsHtml"], (
        "a model judgement is about the whole event, not one field")


def test_a_new_check_opens_on_a_real_field(vocabulary):
    """It reads as a sentence before anything is typed.

    The row used to start with an empty field, which `builderConditions` then
    correctly dropped — so adding a check, filling in the value and saving gave
    you an automation with no check and nothing on screen saying why.
    """
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "value", "value": "invoice"},
    ])
    assert len(out["conditions"]) == 1
    assert out["conditions"][0]["field"], "the row opened with no field"


def test_a_condition_with_its_field_cleared_is_still_not_saved(vocabulary):
    """The guard underneath. A condition reading an empty path never matches,
    so the automation silently never runs — dropping it is the honest answer,
    and it must survive the field becoming a dropdown."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "contains"},
        {"op": "type", "row": 0, "what": "field", "value": ""},
        {"op": "type", "row": 0, "what": "value", "value": "invoice"},
    ])
    assert out["conditions"] == []


def test_typing_in_one_row_survives_adding_another(vocabulary):
    """The bug this shape avoids: re-rendering from the DOM instead of from
    state loses whatever was typed but not yet saved."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "field", "value": "event.from"},
        {"op": "type", "row": 0, "what": "value", "value": "keep me"},
        {"op": "add"},
    ])
    assert 'value="keep me"' in out["conditionsHtml"]
    assert 'value="event.from"' in out["conditionsHtml"]


# ── the trigger, and the legacy columns written from it ─────────────────────

def test_an_event_trigger_names_the_kind_and_the_source(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"},
        {"op": "set", "sel": "#rmEventKind", "value": "issue.changed"},
        {"op": "set", "sel": "#rmEventSource", "value": "linear"},
    ])
    assert out["trigger"] == {"type": "event", "kind": "issue.changed",
                              "source": "linear"}
    assert out["words"] == "An issue changes in linear"


def test_an_event_with_no_legacy_equivalent_falls_back_to_never_firing(vocabulary):
    """The legacy `trigger` column is the fallback for a row whose spec is
    missing. "A GitHub issue changed" has no legacy word, and guessing
    `new_email` would make the fallback fire on mail instead — so it becomes
    `manual`, which fires on nothing, the same choice `_legacy_trigger` makes."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"},
        {"op": "set", "sel": "#rmEventKind", "value": "issue.changed"},
    ])
    assert out["legacy"]["trigger"] == "manual"


def test_an_email_trigger_keeps_the_legacy_word_it_always_had(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"},
        {"op": "set", "sel": "#rmEventKind", "value": "email.received"},
    ])
    assert out["legacy"]["trigger"] == "new_email"


def test_a_time_of_day_is_stored_both_ways_and_they_agree(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "schedule"},
        {"op": "set", "sel": "#rmAtTime", "value": "07:30"},
        {"op": "set", "sel": "#rmDays", "value": "mon,tue,wed,thu,fri"},
    ])
    assert out["trigger"] == {"type": "schedule", "at_time": "07:30",
                              "days": "mon,tue,wed,thu,fri"}
    assert out["legacy"] == {"trigger": "daily", "at_time": "07:30",
                             "days": "mon,tue,wed,thu,fri", "interval_min": 60}
    assert out["words"] == "Weekdays at 7:30 AM"


def test_an_interval_is_stored_both_ways(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "interval"},
        {"op": "set", "sel": "#rmInterval", "value": "15"},
    ])
    assert out["trigger"] == {"type": "interval", "interval_min": 15}
    assert out["legacy"]["trigger"] == "schedule"
    assert out["legacy"]["interval_min"] == 15
    assert out["words"] == "Every 15 min"


# ── editing one that already exists ────────────────────────────────────────

def test_editing_loads_what_the_automation_already_says(vocabulary):
    existing = {"id": "a1", "trigger": {"type": "schedule", "at_time": "09:15",
                                        "days": "sat,sun"},
                "conditions": [{"type": "contains", "field": "event.body",
                                "value": "urgent"}]}
    out = drive(vocabulary, [{"op": "open", "existing": {"id": "a1"}}],
                automation=existing)
    assert out["trigger"] == {"type": "schedule", "at_time": "09:15",
                              "days": "sat,sun"}
    assert out["conditions"] == [{"type": "contains", "field": "event.body",
                                  "value": "urgent"}]


def test_a_nested_rule_is_locked_rather_than_flattened(vocabulary):
    """The state-loss case. An automation set up with `any of these` cannot be
    shown as a flat list, and flattening it on open would change what it does
    without the user touching anything. So the form says so and sends nothing
    for conditions."""
    existing = {"id": "a2", "trigger": {"type": "manual"},
                "conditions": [{"type": "any", "conditions": [
                    {"type": "contains", "field": "event.body", "value": "a"},
                    {"type": "contains", "field": "event.body", "value": "b"}]}]}
    out = drive(vocabulary, [{"op": "open", "existing": {"id": "a2"}},
                             {"op": "save", "id": "a2"}],
                automation=existing)
    assert out["conditions"] is None
    assert out["addHidden"] is True
    assert "grouped rule" in out["hint"]
    patch = [c for c in out["calls"] if c["method"] == "PATCH"]
    assert patch and "conditions" not in patch[0]["body"], (
        "it sent conditions over a rule it could not read")


def test_saving_sends_the_trigger_and_the_conditions_together(vocabulary):
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "interval"},
        {"op": "set", "sel": "#rmInterval", "value": "30"},
        {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "is_true"},
        {"op": "type", "row": 0, "what": "field", "value": "event.labels"},
        {"op": "save", "id": "a9"},
    ])
    assert out["saved"] == ""
    patch = [c for c in out["calls"] if c["method"] == "PATCH"]
    assert len(patch) == 1
    assert patch[0]["url"].endswith("/api/automations/a9")
    assert patch[0]["body"]["trigger"] == {"type": "interval", "interval_min": 30}
    assert patch[0]["body"]["conditions"] == [
        {"type": "is_true", "field": "event.labels"}]


def test_the_form_still_opens_when_the_vocabulary_cannot_be_fetched(vocabulary):
    """First launch on a machine where that call failed. An empty select is a
    screen the user cannot create an automation on."""
    out = drive({}, [{"op": "open"}])
    assert 'value="schedule"' in out["triggerHtml"]
    assert 'value="event"' in out["triggerHtml"]


# ── pressing Create, through the button the page wired ──────────────────────

def test_creating_one_writes_the_routine_row_and_then_the_spec(vocabulary):
    """The real click handler, not a reimplementation of it.

    Two requests, in this order: the routine row exists first, and the trigger
    spec is written against the id it came back with. The legacy columns on the
    first request are derived from the same answer as the spec on the second, so
    a row whose `trigger_json` is ever lost still falls back to something that
    means the same thing.
    """
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "schedule"},
        {"op": "set", "sel": "#rmAtTime", "value": "06:45"},
        {"op": "set", "sel": "#rmDays", "value": "sat,sun"},
        {"op": "add"},
        {"op": "type", "row": 0, "what": "type", "value": "domain_is"},
        {"op": "type", "row": 0, "what": "field", "value": "event.from"},
        {"op": "type", "row": 0, "what": "value", "value": "acme.com"},
        {"op": "create", "name": "Weekend digest"},
    ])
    writes = [c for c in out["calls"] if c["method"] in ("POST", "PATCH")]
    assert [c["method"] for c in writes] == ["POST", "PATCH"]

    posted = writes[0]
    assert posted["url"] == "/api/routines"
    assert posted["body"]["name"] == "Weekend digest"
    assert posted["body"]["trigger"] == "daily"
    assert posted["body"]["at_time"] == "06:45"
    assert posted["body"]["days"] == "sat,sun"

    patched = writes[1]
    assert patched["url"] == "/api/automations/new-1"
    assert patched["body"]["trigger"] == {"type": "schedule", "at_time": "06:45",
                                          "days": "sat,sun"}
    assert patched["body"]["conditions"] == [
        {"type": "domain_is", "field": "event.from", "value": "acme.com"}]

    assert out["modalHidden"] is True
    assert out["toasts"] == ["Automation created"]


def test_a_spec_that_could_not_be_stored_is_not_reported_as_success(vocabulary):
    """The honest failure. The routine row exists — so "Could not save" would be
    wrong — but what it checks did not land, and a green toast over that is the
    app lying about the user's own automation."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "interval"},
        {"op": "create"},
    ], fail_patch=True)

    assert [c["method"] for c in out["calls"] if c["method"] == "POST"] == ["POST"]
    assert out["toasts"], "it said nothing at all"
    told = out["toasts"][-1]
    assert "could not be stored" in told
    assert told != "Automation created"


# ── the shape of the form itself ───────────────────────────────────────────

def test_the_app_is_picked_from_a_list_by_its_real_name(vocabulary):
    """It was a text box you typed an app's id into.

    That asked people to remember that Google Calendar is spelled `gcal`, and a
    typo in it is an automation that never fires with nothing on screen saying
    why.
    """
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"},
        {"op": "set", "sel": "#rmEventKind", "value": "email.received"},
    ])
    assert "Any connected app" in out["sourceHtml"]
    assert 'value="gmail"' in out["sourceHtml"]
    assert ">Gmail<" in out["sourceHtml"], "it shows the id instead of the name"


def test_only_the_apps_that_can_cause_that_event_are_offered(vocabulary):
    """A menu of fifteen apps where two are possible is a menu somebody picks
    wrongly from — and "a calendar event changes, in Gmail" is an automation
    that can never fire."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"},
        {"op": "set", "sel": "#rmEventKind", "value": "calendar.changed"},
    ])
    assert ">Google Calendar<" in out["sourceHtml"]
    assert ">Apple Calendar<" in out["sourceHtml"]
    assert ">Gmail<" not in out["sourceHtml"]


def test_changing_the_event_drops_an_app_that_no_longer_fits(vocabulary):
    """Pick Gmail, then change your mind to calendars. Leaving Gmail selected
    would save an automation that silently never runs."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"},
        {"op": "set", "sel": "#rmEventKind", "value": "email.received"},
        {"op": "set", "sel": "#rmEventSource", "value": "gmail"},
        {"op": "set", "sel": "#rmEventKind", "value": "calendar.changed"},
    ])
    assert out["trigger"] == {"type": "event", "kind": "calendar.changed"}, (
        "it kept an app that cannot produce this event")


def test_how_often_is_a_list_of_spans_not_a_number_box(vocabulary):
    """"Every (minutes): 1440" is a unit conversion the user should not be
    doing, and it sat beside an empty cell where every other step has a second
    field."""
    out = drive(vocabulary, [{"op": "open"}])
    assert "Every hour" in out["everyHtml"]
    assert "Once a day" in out["everyHtml"]
    assert 'value="1440"' in out["everyHtml"]


def test_an_interval_the_list_does_not_offer_is_kept(vocabulary):
    """An automation set to 45 minutes must not become 15 because somebody
    opened it to read the name."""
    existing = {"id": "a4", "trigger": {"type": "interval", "interval_min": 45},
                "conditions": []}
    out = drive(vocabulary, [{"op": "open", "existing": {"id": "a4"}}],
                automation=existing)
    assert out["trigger"] == {"type": "interval", "interval_min": 45}
    assert "Every 45 minutes" in out["everyHtml"]


def test_the_form_is_three_numbered_steps():
    """It was ten labels in one column: the same information, with nothing
    showing which box belonged to which idea.

    Read off the markup, because this is the one claim about the form that is
    structural rather than behavioural — and the harness renders into a fake
    DOM that has no layout to measure.
    """
    html = (WEB / "index.html").read_text()
    modal = html.split('id="routineModal"')[1].split("</div>\n\n")[0]

    steps = modal.count('class="am-step"')
    assert steps == 3, f"the builder has {steps} steps, not three"
    for number in ("1", "2", "3"):
        assert f'class="am-step-n">{number}<' in modal
    for heading in ("When", "Only if", "Do this"):
        assert heading in modal

    # The three questions a person actually asks, in the order they ask them.
    when = modal.index('class="am-step-n">1<')
    only_if = modal.index('class="am-step-n">2<')
    do_this = modal.index('class="am-step-n">3<')
    assert when < only_if < do_this


def test_every_check_lays_out_on_the_same_grid():
    """The symmetry claim, checked where it is decided.

    With `flex: 1 1 28%` a check that takes no value spread two boxes across
    the width its neighbours used for three, so nothing in the list lined up
    with anything below it. A grid with fixed columns is what fixes that, and
    the placeholders are why an empty column still holds its place.
    """
    css = (WEB / "styles.css").read_text()
    row = css.split(".cond-row {")[1].split("}")[0]

    assert "display: grid" in row
    assert "grid-template-columns" in row
    assert "flex:" not in row
    assert ".cond-whole, .cond-none" in css, (
        "a row with no field or no value would collapse its column")


def test_a_check_can_always_be_removed_on_a_narrow_window():
    """The remove button used to be pushed off-screen by a row that overflowed,
    which made a check impossible to delete."""
    css = (WEB / "styles.css").read_text()
    assert "@media (max-width: 560px)" in css
    narrow = css.split("@media (max-width: 560px)")[1]
    assert ".cond-row { grid-template-columns: 1fr 1fr; }" in narrow


def test_a_value_with_known_answers_is_a_list(vocabulary):
    """"Which app it came from" is a choice between the apps this build has,
    not a sentence — and a text box there is a box somebody types "Gmail" into
    while the engine compares against "gmail"."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "field", "value": "event.source"},
        {"op": "type", "row": 0, "what": "type", "value": "equals"},
        {"op": "type", "row": 0, "what": "value", "value": "gmail"},
    ])
    assert "<select class=\"cond-value\"" in out["conditionsHtml"]
    assert ">Gmail<" in out["conditionsHtml"]
    assert out["conditions"] == [
        {"type": "equals", "field": "event.source", "value": "gmail"}]


def test_a_value_with_no_known_answers_is_still_a_box(vocabulary):
    """Most of them. "Contains the word invoice" cannot be a menu."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "field", "value": "event.title"},
        {"op": "type", "row": 0, "what": "type", "value": "contains"},
        {"op": "type", "row": 0, "what": "value", "value": "invoice"},
    ])
    assert "<input class=\"cond-value\"" in out["conditionsHtml"]
    assert out["conditions"] == [
        {"type": "contains", "field": "event.title", "value": "invoice"}]


def test_changing_the_field_clears_a_value_that_no_longer_means_anything(
        vocabulary):
    """`gmail` picked for "which app" is not an answer to "subject contains".
    Carried across, it would read as a check the user never wrote."""
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "add"},
        {"op": "type", "row": 0, "what": "field", "value": "event.source"},
        {"op": "type", "row": 0, "what": "value", "value": "gmail"},
        {"op": "type", "row": 0, "what": "field", "value": "event.title"},
    ])
    assert out["conditions"] == [], "the old value survived into a new question"
    assert "gmail" not in out["conditionsHtml"]


# ── the menu says what each choice is for ──────────────────────────────────

def test_the_when_menu_explains_the_choice(vocabulary):
    """Four names do not say which one to pick.

    A user wanting "run when the mail arrives" chose **At a time of day**, then
    asked for an "any time" option — which cannot exist, because for that
    trigger the time *is* the rule. The answer was the first item in the same
    menu, and nothing on screen said so.
    """
    out = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "event"}])
    assert "do not know when" in out["triggerHint"], (
        "the option for 'I don't know when it is coming' does not say so")


def test_the_explanation_follows_the_choice(vocabulary):
    """A line that stays put while the menu moves is worse than none — it
    describes the wrong thing with the authority of being on screen."""
    clock = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "schedule"}])
    asked = drive(vocabulary, [
        {"op": "open"}, {"op": "trigger", "value": "manual"}])

    assert "at a time you pick" in clock["triggerHint"].lower()
    assert "never runs on its own" in asked["triggerHint"].lower()
    assert clock["triggerHint"] != asked["triggerHint"]


def test_every_trigger_says_what_it_is_for(vocabulary):
    """A trigger added with no explanation shows an empty line where every
    other one explains itself."""
    assert all(t["hint"] for t in vocabulary["triggers"])


@pytest.mark.parametrize("trigger,shown", [
    ("event", "event"), ("schedule", "schedule"), ("interval", "interval"),
])
def test_only_the_questions_that_trigger_asks_are_shown(vocabulary, trigger,
                                                        shown):
    """A time box under "something happens in an app" is a setting the user
    filled in that does nothing."""
    out = drive(vocabulary, [{"op": "open"}, {"op": "trigger", "value": trigger}])
    assert out["showing"] == {"event": shown == "event",
                              "schedule": shown == "schedule",
                              "interval": shown == "interval"}


def test_asking_for_it_yourself_shows_no_questions_at_all(vocabulary):
    out = drive(vocabulary, [{"op": "open"}, {"op": "trigger", "value": "manual"}])
    assert out["showing"] == {"event": False, "schedule": False,
                              "interval": False}
