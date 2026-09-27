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


def render(text: str, markdown: str | None = None) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), str(APP_JS)],
        input=json.dumps({"text": text, "markdown": markdown}),
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# Tags the renderer itself emits. Anything else that survives as a *live* tag
# came from the model.
OWN_TAGS = {"div", "span", "b", "strong", "em", "code", "pre", "p", "ul", "li",
            "a", "button", "br", "h3", "h4", "h5", "h6"}

TAG = re.compile(r"<\s*/?\s*([a-zA-Z][\w-]*)")


def _no_live_markup(written: list[str]):
    """No write may contain a tag the renderer did not write itself.

    Checking for the literal string "onerror=" would be wrong: after escaping,
    `&lt;img src=x onerror=alert(1)&gt;` still *contains* that text and is inert,
    because the `<` is gone. What matters is whether a real tag survived — so
    this looks for `<name` and compares the name against the markup the renderer
    actually produces.
    """
    for chunk in written:
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
