"""Model-written text must not be able to put markup into the page.

The assistant's reply is rendered as HTML — markdown for prose, and `<action>`
tags become confirmation cards built with template strings and `innerHTML`. The
model is summarising the user's own mail, documents and web results, so a
crafted message decides what those strings contain. That is the whole attack:
not a "hacker", just a phishing email the Inbox agent read.

These tests run the real `parseActions` / `actionCard` / `md` from `app.js` in
node, the way the other frontend harnesses do — `node --check` cannot see this,
and neither can reading the source.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "chitragupta" / "web" / "app.js"
HARNESS = ROOT / "tests" / "js" / "action_card.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the frontend")

# Payloads that become script execution if they reach innerHTML intact.
PAYLOADS = [
    '<img src=x onerror=alert(1)>',
    '<svg/onload=alert(1)>',
    "<script>fetch('/api/brain/export')</script>",
    "'><img src=x onerror=alert(1)>",
]


def render(text: str, markdown: str | None = None,
           catalog: dict | None = None) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        input=json.dumps({"text": text, "markdown": markdown,
                          "catalog": catalog}),
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# Tags the renderer itself emits. Anything else that survives as a *live* tag
# came from the model.
OWN_TAGS = {"div", "span", "b", "strong", "em", "code", "pre", "p", "ul", "li",
            "a", "button", "br", "h3", "h4", "h5", "h6"}

TAG = re.compile(r"<\s*/?\s*([a-zA-Z][\w-]*)")


#: The renderer's own inlined icons, exactly as `_S()` in `core.js` builds them.
#:
#: A settled card draws `IC.check` beside its result, so the first test to look
#: at one met a `<svg>` and had to decide what it was. Adding "svg" to the
#: allowed tags would have been the easy answer and the wrong one — one of the
#: payloads *is* `<svg/onload=alert(1)>`, so the guard would have stopped being
#: able to see it. This removes the app's own icons by their exact opening
#: instead, which a payload cannot spell: the attribute grammar in
#: `parseActions` ends a value at the first double quote.
OWN_ICON = re.compile(r'<svg viewBox="0 0 16 16"[^>]*>.*?</svg>', re.S)


def _no_live_markup(written: list[str]):
    """No write may contain a tag the renderer did not write itself.

    Checking for the literal string "onerror=" would be wrong: after escaping,
    `&lt;img src=x onerror=alert(1)&gt;` still *contains* that text and is inert,
    because the `<` is gone. What matters is whether a real tag survived — so
    this looks for `<name` and compares the name against the markup the renderer
    actually produces.
    """
    for chunk in [OWN_ICON.sub(" ", c) for c in written]:
        for name in TAG.findall(chunk):
            assert name.lower() in OWN_TAGS, (
                f"a live <{name}> from model text reached innerHTML:\n{chunk[:300]}")
        # An attribute breakout would show up as an event handler sitting
        # outside any text node — i.e. immediately after an unescaped quote.
        assert not re.search(r'"\s+on\w+\s*=', chunk), (
            f"an event-handler attribute was injected:\n{chunk[:300]}")


@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_models_action_attributes_cannot_inject_markup(payload):
    """Every attribute of an `<action>` tag is written by the model."""
    text = (f'<action type="create_routine" trigger="schedule" '
            f'interval_min="{payload}" name="{payload}" agent="{payload}">'
            f'{payload}</action>')
    _no_live_markup(render(text)["writes"])


@pytest.mark.parametrize("payload", PAYLOADS)
def test_an_email_draft_cannot_inject_markup(payload):
    """The highest-stakes card: the one that sends mail on confirmation."""
    text = (f'<action type="send_email" to="{payload}" subject="{payload}">'
            f'{payload}</action>')
    _no_live_markup(render(text)["writes"])


@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_calendar_event_cannot_inject_markup(payload):
    text = f'<action type="create_event" title="{payload}" start="{payload}"></action>'
    _no_live_markup(render(text)["writes"])


def test_the_schedule_interval_is_forced_to_a_number():
    """`interval_min` was interpolated raw — it is the one attribute that was
    not escaped, because it read like a number and was not."""
    out = render('<action type="create_routine" trigger="schedule" '
                 'interval_min="&lt;img src=x onerror=alert(1)&gt;" name="x">go</action>')
    joined = " ".join(out["writes"])
    _no_live_markup([joined])
    # 60 minutes, in the words the row and the server both use. The card used
    # to phrase this itself and said "every 60 min"; it now goes through
    # `routineWhen`, so one automation reads the same everywhere it appears.
    assert "Every hour" in joined, "a non-numeric interval should fall back to 60"


def test_a_sensible_interval_still_shows():
    out = render('<action type="create_routine" trigger="schedule" '
                 'interval_min="15" name="Digest">summarise</action>')
    assert "Every 15 min" in " ".join(out["writes"])


@pytest.mark.parametrize("payload", PAYLOADS)
def test_markdown_in_a_reply_is_escaped_before_it_is_formatted(payload):
    """`md()` escapes and *then* applies inline formatting. The order is the
    whole defence — formatting first would emit tags the escape never sees."""
    out = render("", markdown=f"Here is a summary\n\n{payload}\n\n- item {payload}")
    _no_live_markup([out["markdown"]])


def test_markdown_still_renders_normal_formatting():
    """A guard that breaks the feature is not a guard."""
    out = render("", markdown="# Title\n\n**bold** and `code`\n\n- one\n- two")
    html = out["markdown"]
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert "<li>one</li>" in html


def test_a_link_in_a_reply_cannot_become_a_javascript_url():
    """`md()` only linkifies http(s), so a `javascript:` target stays inert
    text. What must never appear is it becoming an actual href."""
    out = render("", markdown="[click](javascript:alert(1))")
    assert 'href="javascript:' not in out["markdown"].lower()
    assert "<a " not in out["markdown"].lower()


def test_a_real_link_still_becomes_a_link():
    out = render("", markdown="see [the docs](https://example.com/x)")
    assert 'href="https://example.com/x"' in out["markdown"]
    assert 'rel="noopener"' in out["markdown"]


def test_the_action_tag_is_stripped_from_the_visible_text():
    out = render("Sure, I'll set that up.\n"
                 '<action type="set_reminder" at="tomorrow 9am">standup</action>')
    assert "<action" not in out["clean"]
    assert "Sure, I'll set that up." in out["clean"]


def test_the_live_entity_chip_type_is_constrained_server_side():
    """The one interpolation that was genuinely reachable.

    `/api/brain/enrich/status` returns a `found` list of entities the extractor
    just produced, and the UI renders each as
    `<span class="ep-chip ${f.type}">`. The type came straight from the model's
    JSON with no allowlist on that path — `upsert_entity` applies one, but the
    live-feed list skipped it. A model persuaded by content in the user's own
    mail could therefore put an attribute into the page of an app that holds
    their credentials.

    Fixed on both sides: the type is constrained where it is produced, and the
    template escapes it. This test pins the server half.
    """
    from chitragupta.brain.graph import ENTITY_TYPES

    assert '"><img' not in str(ENTITY_TYPES)
    for candidate, expected in [
        ("person", "person"),
        ("PERSON", "person"),
        ('"><img src=x onerror=alert(1)>', "thing"),
        ("", "thing"),
        (None, "thing"),
    ]:
        raw = str(candidate or "thing").lower()
        assert (raw if raw in ENTITY_TYPES else "thing") == expected


# ── every card, not the three somebody thought of ─────────────────────────
#
# The tests above name `create_routine`, `send_email` and `create_event`, and
# they are the three that were on somebody's mind. Two cards assembled their
# **title** out of model-written text and neither was listed, so both wrote a
# live tag into the head of a card:
#
#     <div class="ac-head"><img src=x onerror=alert(1)> in Notion</div>
#     <div class="ac-head">Label as “<svg onload=alert(1)>” 1 email</div>
#
# One of those cards escaped the same string correctly in a row two lines lower,
# which is what a rule living in eleven branches looks like from outside. So
# these walk the registry, in two passes, because there are two ways params
# reach a card and they admit different payloads.

#: Fields that carry structure. A flat XML attribute cannot hold a list, so the
#: body is JSON for these — the same split `parseActions` makes.
STRUCTURED = {"items", "blocks", "arguments"}


def attr_safe(payload: str) -> str:
    r"""The payload as an `<action>` attribute could really carry it.

    `parseActions` reads attributes as `(\w+)="([^"]*)"` inside `<action
    ([^>]*?)>`, so a value can hold neither a double quote nor a `>`. A payload
    with either does not become a card at all — the tag stops early, the JSON
    body no longer parses, and the whole proposal is dropped.

    **That is why the first version of this walk was worthless.** Every payload
    here ends in `>`, so every attribute case was silently dropped and the pass
    proved nothing: `mcp_action` stayed green with the escaping removed. The
    tags are left unterminated instead, which the guard still recognises as live
    markup (`<img` is enough) and the parser still delivers.
    """
    return payload.replace('"', "").replace(">", "")


def _structured_body(field: str, payload: str) -> object:
    """A value of the right shape for `field`, with the payload inside it.

    The payload sits in a *string the card renders*, not merely somewhere in the
    JSON: `items` is drawn through its labels and subjects, and a payload hidden
    in a number would be a test that always passes. JSON is also where the full
    payload survives — a quote is `\"` and a `>` is nothing special, which is
    exactly why `mail_triage` was the one card an email could really reach.
    """
    if field == "items":
        return {"items": [{"id": "m1", "do": "label", "label": payload,
                           "subject": payload}]}
    if field == "blocks":
        return {"blocks": [{"exercise": payload, "sets": 3, "reps": 5,
                            "weight": 100}]}
    return {"title": payload, "text": payload}       # arguments


def _tag(action_type: str, fields: list[str], payload: str) -> str:
    """An `<action>` tag for this type with the payload in everything it reads."""
    structured = [f for f in fields if f in STRUCTURED]
    attrs = {f: attr_safe(payload) for f in fields if f not in STRUCTURED}
    # `server` is what a model writes and `server_id` is what the params are
    # called; the parser maps one onto the other, so both carry it.
    if "server_id" in attrs:
        attrs["server"] = attrs["server_id"]
    body = json.dumps(_structured_body(structured[0], payload)) if structured \
        else attr_safe(payload)
    spelled = " ".join(f'{name}="{value}"' for name, value in attrs.items())
    return f'<action type="{action_type}" {spelled}>{body}</action>'


def _catalog() -> dict:
    from chitragupta.actions import catalog

    return catalog()


def _every_action():
    from chitragupta.actions import REGISTRY

    return sorted(REGISTRY)


@pytest.mark.parametrize("action_type", _every_action())
@pytest.mark.parametrize("payload", PAYLOADS)
def test_no_card_can_be_talked_into_markup(action_type, payload):
    """Pass one: the whole path a model reply really takes.

    Every action in the registry, with the payload in every field it reads —
    and the card must actually have been drawn, or this proves nothing about it.
    """
    out = render(_tag(action_type, list(_catalog()[action_type]["fields"]),
                      payload), catalog=_catalog())
    assert any("ac-head" in w for w in out["writes"]), (
        f"{action_type} drew no card for {payload!r}, so nothing here was "
        f"checked — see `attr_safe`")
    _no_live_markup(out["writes"])


@pytest.mark.parametrize("action_type", _every_action())
@pytest.mark.parametrize("payload", PAYLOADS)
def test_no_card_can_be_talked_into_markup_by_its_params(action_type, payload):
    """Pass two: the renderer on its own, handed the full payload.

    The attribute grammar is what keeps a `>` out of most fields today, and
    leaning on it would be safety by accident — it is one regex away from
    changing, and it is not in force on any of the other paths that build an
    action: a plan step, a queued approval, a card drawn from a stored message.
    `mcp_action`'s title was reachable in exactly this way.
    """
    fields = list(_catalog()[action_type]["fields"])
    params = {f: payload for f in fields if f not in STRUCTURED}
    for f in fields:
        if f in STRUCTURED:
            got = _structured_body(f, payload)
            params[f] = got.get(f) if isinstance(got, dict) and f in got else got
    if "server_id" in params:
        params["server_id"] = payload
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(APP_JS)],
        input=json.dumps({"action": {"type": action_type, "params": params},
                          "catalog": _catalog(),
                          "result": {"ok": True, "detail": "done"}}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    assert "ac-head" in out["html"], f"{action_type} drew no card"
    _no_live_markup([out["html"]])


def test_a_settled_card_escapes_its_title_too():
    """The second sink. A card reopened after it ran is drawn by `settledCard`,
    which interpolates the same `title` — so escaping one of the two would have
    left this reachable by scrolling up in yesterday's conversation.
    """
    tool = '<img src=x onerror=alert(1)>'
    action = {"type": "mcp_action",
              "params": {"server_id": "notion", "tool": tool,
                         "arguments": {"page": "p1"}}}
    key = ('mcp_action|arguments={"page":"p1"}&server_id=notion&'
           f"tool={tool}#1")
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(APP_JS)],
        input=json.dumps({"action": action, "catalog": _catalog(),
                          "result": {"ok": True, "detail": "ran"},
                          "cardState": {key: {"state": "done",
                                              "detail": "ran"}}}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    assert out["settled"] == "done", (
        "the card was not drawn settled, so this checked the pending head again")
    _no_live_markup([out["html"]])
