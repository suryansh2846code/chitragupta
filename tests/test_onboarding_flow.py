"""The first screen a person ever sees, and whether they can get off it.

`test_onboarding_digest.py` covers what the finale is allowed to CLAIM. This
file covers everything in front of it: what the Continue gate accepts, what a
keyboard can reach, what the page erases and when it asks first, and whether the
one accent in `docs/DESIGN-BRIEF.md` survives the page the user meets first.

Every defect pinned here shipped. They are gathered in one file because they
share two roots, and the tests are grouped by those roots rather than by
symptom:

  * **The panes are layered, not switched.** `opacity:0; pointer-events:none`
    hides a screen from the eyes and the mouse and from nothing else — `Tab`
    still walked every control on every screen. On the finale, five of seven tab
    stops were invisible, and one of them erases the brain.
  * **The gate asked about shape, not readiness.** A connector NAME in a dict
    and a provider STRING in localStorage, neither of which is the question that
    matters: is there anything to read, and can the model answer.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"
PAGE = WEB / "onboarding.html"
SRC = PAGE.read_text()
CORE = (WEB / "core.js").read_text()

#: The page WITHOUT its commentary. Several rules here are about what the user
#: is shown, and this file's own comments quote the copy and the glyphs they
#: exist to keep out — a test that reads them is testing its own prose.
CODE = re.sub(r"/\*.*?\*/", "", re.sub(r"<!--.*?-->", "", SRC, flags=re.S), flags=re.S)
CODE = "\n".join(re.sub(r"(^|\s)//[:>< ].*$", "", ln) for ln in CODE.split("\n"))


def _run(script: str, plan: dict) -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js" / script), str(PAGE)],
        input=json.dumps(plan), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# ── the gate: one source, and a model that can actually answer ──────────────
def gate(**plan) -> dict:
    plan.setdefault("selected", {})
    plan.setdefault("llmReady", False)
    plan.setdefault("provider", None)
    return _run("onboarding_gate.mjs", plan)


def test_any_source_is_enough_to_build_a_brain():
    """The regression: `canContinue` required `selected["gmail"]`.

    Nothing downstream does. A user on Apple Mail, on Outlook, or on notes
    alone connected everything they own and watched Continue stay dead, with a
    gold hint naming a product they had not got and no way past it — the header
    "Skip for now" is hidden the moment the connect screen appears.
    """
    for source in ("notion", "files", "github", "apple_mail", "imessage"):
        out = gate(selected={source: True}, llmReady=True, provider="ollama")
        assert out["canContinue"] is True, f"{source} alone should be enough"
        assert out["hint"] == ""


def test_no_source_at_all_is_not_enough():
    out = gate(selected={}, llmReady=True, provider="ollama")
    assert out["canContinue"] is False
    assert "a source" in out["hint"]


def test_a_provider_that_cannot_answer_does_not_open_the_gate():
    """The regression: the gate read a STRING out of localStorage.

    Pick a cloud provider, leave the key box empty, press Save — the string is
    written, the button goes gold, and three minutes later the finale renders
    `reason: "no_model"` and says "Connect an AI model and this is written from
    your own data" to somebody who believes they just did.
    """
    out = gate(selected={"notion": True}, llmReady=False, provider="anthropic")
    assert out["llmName"] == "anthropic", "the choice is still remembered"
    assert out["canContinue"] is False, "a chosen-but-unready provider opened the gate"
    assert "working AI model" in out["hint"], out["hint"]


def test_the_hint_names_everything_that_is_missing():
    out = gate(selected={}, llmReady=False, provider=None)
    assert out["canContinue"] is False
    assert "a source" in out["hint"] and "an AI model" in out["hint"]
    #: Never an internal — no connector key, no provider id, no endpoint.
    assert "gmail" not in out["hint"].lower()


# ── what a keyboard can reach on each screen ────────────────────────────────
def panes(stages: list[str], inert_supported: bool = True,
          account_done: bool = True) -> dict:
    return _run("onboarding_panes.mjs",
                {"stages": stages, "inertSupported": inert_supported,
                 "accountDone": account_done})


#: Which screen is live → the only controls a keyboard may land on.
#:
#: Every entry here is a screen *after* the account step, which is why they pass
#: `accountDone`. The account screen is first and is covered on its own below.
REACHABLE = {
    ("hero", ()): {"buildBtn"},
    ("connect", ("on",)): {"sourceTile", "continue"},
    ("build", ("on", "building")): {"cancel", "bSkip"},
    ("digest", ("on", "brainready")): {"toBrain"},
}


@pytest.mark.parametrize("name,stages", [(k[0], k[1]) for k in REACHABLE])
def test_only_the_live_screen_is_reachable(name, stages):
    """The regression, and the worst one on the page.

    `#buildBtn` calls `startFromZero()` → `POST /api/brain/reset
    {memories, secrets, google}`. It stayed in the tab order on every later
    screen, so a keyboard user shift-tabbing on the finale could erase the brain
    they had just waited three minutes for, plus every credential.
    """
    out = panes(list(stages))
    assert set(out["reachable"]) == REACHABLE[(name, stages)], (
        f"on the {name} screen a keyboard reaches {sorted(out['reachable'])}")


# ── the account screen, which is now the first one ──────────────────────────

def test_the_account_screen_is_the_first_one():
    """Making an account is step one — a deliberate product decision, recorded
    in `/CLAUDE.md` and `docs/ACCOUNTS-DESIGN.md` §0. Before it, the hero was
    first and `#buildBtn` — which **erases the brain** — was the first control a
    keyboard could reach on a fresh install."""
    out = panes([], account_done=False)
    assert set(out["reachable"]) == {"acoButtons"}
    assert "buildBtn" not in out["reachable"], \
        "the brain-erasing button is reachable on the very first screen"


def test_the_account_screen_has_no_way_around_it():
    """The decision this screen exists to carry: signing in is the way past it."""
    assert set(panes([], account_done=False)["reachable"]) == {"acoButtons"}


def test_passing_the_account_step_hands_over_to_the_hero():
    before = panes([], account_done=False)["reachable"]
    after = panes([], account_done=True)["reachable"]
    assert "acoButtons" in before and "buildBtn" not in before
    assert "buildBtn" in after and "acoButtons" not in after


def test_the_account_screen_is_hidden_from_assistive_tech_once_passed():
    assert "paneAccount" not in panes([], account_done=True)["exposed"]
    assert "paneAccount" in panes([], account_done=False)["exposed"]


def test_the_reset_button_is_gone_once_onboarding_has_started():
    """Stated on its own, because it is the one with teeth."""
    for stages in (["on"], ["on", "building"], ["on", "brainready"]):
        assert "buildBtn" not in panes(stages)["reachable"], stages


def test_a_hidden_screen_is_hidden_from_assistive_tech_too():
    out = panes(["on", "brainready"])
    for pane in ("paneHero", "paneConnect", "paneBuild"):
        assert pane not in out["exposed"], f"{pane} is still in the a11y tree"
    assert "paneDigest" in out["exposed"]


def test_visibility_is_the_fallback_where_inert_is_unsupported():
    """`inert` is the right tool and it is not everywhere. The fallback has to
    remove the subtree from the tab order too, or it is decoration."""
    out = panes(["on", "brainready"], inert_supported=False)
    assert set(out["reachable"]) == {"toBrain"}


def test_no_screen_offers_a_skip():
    """**Reversed deliberately, and this test is the record of it.**

    The rule here used to be "a way out exists on every screen after the
    account step", carried by a `Skip for now →` pill in the header that was
    live on the hero and on connect. Setup is not dismissible in a shipping
    build, so the pill is gone and the rule is now its opposite: *no* screen
    offers a way to abandon onboarding.

    What survives is a different thing, and the distinction is the whole
    decision. A **skip** abandons setup and enters the app anyway. An **escape
    hatch** fires when a step cannot be completed at all, and there are exactly
    two — both asserted below and neither reachable on demand.

    This asserts the control is gone from the *markup*, not merely unreachable:
    `panes()` reports what a keyboard can land on, and a button nothing makes
    live would pass that check while still being one `setLive` away from
    coming back.

    Comments are stripped before the check, and deliberately — the prose that
    explains why the pill went says its name, and a test that cannot tell a
    button from the sentence recording its removal would forbid writing the
    reason down.
    """
    page = (ROOT / "chitragupta/web/onboarding.html").read_text()
    code = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "skipTop" not in code, "the skip pill is back in the onboarding markup"
    assert "Skip for now" not in code, "the skip pill is back under another id"
    for screen in ([], ["on"], ["on", "building"], ["on", "brainready"]):
        assert "skipTop" not in panes(screen)["reachable"]


def test_the_two_escape_hatches_survive_the_skip_being_removed():
    """Neither is a skip, and removing the skip must not have taken them.

    Without these the removal turns a dismissible setup into a trap, which is
    the bug the skip was originally added to fix:

    - `bSkip` ("Continue anyway →") appears only after 150s of waiting, and
      still lands the user on the digest rather than past it. Covered properly
      in `test_onboarding_digest.py::test_a_long_wait_is_never_a_trap`.
    - `cancel` goes *backwards* to connect, so the build screen is never a
      one-way door.
    - the account screen opens itself when a build genuinely cannot sign in —
      `test_onboarding_account.py::test_a_build_that_cannot_sign_in_does_not_trap_the_user`.
    """
    assert {"cancel", "bSkip"} <= set(panes(["on", "building"])["reachable"])


def test_the_step_rail_says_which_step_it_is_on_without_colour():
    out = panes(["on", "building"])
    assert out["current"] == ["stepBuild"], out["current"]
    assert out["active"] == ["stepBuild"]
    assert out["done"] == ["stepConnect"], "a finished step is not marked as one"


# ── erasing the brain is a decision, not a side effect ──────────────────────
def test_build_my_brain_asks_before_it_erases_anything():
    """`startFromZero` fired the most destructive call in the product with no
    confirmation. Right for a genuine first run; wrong for every other way of
    arriving — and the page binds Cmd+R to reload onto the hero, where that
    button is the only call to action."""
    body = SRC[SRC.index("async function startFromZero"):]
    body = body[:body.index("// hero → connect view")]
    assert "confirmWipe" in body
    assert "somethingToLose" in body
    # …and the wipe is behind it, not beside it.
    guard = body.index("confirmWipe(have)")
    reset = body.index("/api/brain/reset")
    assert guard < reset, "the reset runs before the confirmation"


def test_declining_to_erase_still_lets_the_user_continue():
    """Refusing to erase is not refusing to proceed. Parking the user on the
    hero with no way forward would be a second dead end where the first was."""
    body = SRC[SRC.index("async function startFromZero"):SRC.index("// hero → connect view")]
    branch = body[body.index("if(have.any&&!await confirmWipe(have))"):]
    branch = branch[:branch.index("didReset=true;\n    // truly new-user")]
    assert "/api/brain/reset" not in branch, "the decline path still erases"
    assert "return true" in branch, "the decline path does not continue"


# ── controls that cannot do what they say ──────────────────────────────────
def test_the_finale_has_one_button_not_two_that_do_the_same_thing():
    """"Edit summary later" called `enterApp()` — byte-identical to the primary
    button beside it, and promising an affordance that does not exist."""
    assert "dedit" not in CODE
    assert "Edit summary later" not in CODE
    finale = SRC[SRC.index('<div class="dcont">'):]
    assert finale[:finale.index("</div>")].count("<button") == 1


def test_continue_anyway_answers_the_tap_it_was_given():
    """It set a flag and changed nothing. The next tick is 700ms away and the
    digest behind it can take 30 seconds, so the button a user waited 150
    seconds for appeared to do nothing at all."""
    block = SRC[SRC.index("// >>> build-progress >>>"):SRC.index("// <<< build-progress <<<")]
    handler = block[block.index("skipBtn.onclick="):]
    handler = handler[:handler.index("if(buildIv)clearInterval")]
    assert "disabled=true" in handler
    assert "Finishing" in handler
    assert "say(" in handler, "the screen does not acknowledge the tap"


def test_google_sign_in_can_be_stopped():
    """60 polls × 3s = three minutes with the card frozen at
    "Opening Google sign-in…" and `pointer-events:none` over it. Anything the
    user starts, they can stop."""
    assert "cancelGoogle" in SRC
    assert "tap to cancel" in SRC
    #: …and the busy card must still take the click that cancels it.
    busy = re.search(r"\.src\.busy\{([^}]*)\}", SRC)
    assert busy, "no .src.busy rule"
    assert "pointer-events:none" not in busy.group(1), (
        "a busy card cannot be clicked, so it cannot be cancelled")


def test_a_source_tile_can_be_operated_from_a_keyboard():
    """They were `<div>`s with click handlers, so the connect screen had no
    keyboard path to its own content at all."""
    render = SRC[SRC.index("function renderSources"):SRC.index("// ── AI model")]
    assert 'createElement("button")' in render
    assert 'el.type="button"' in render
    assert "aria-pressed" in render
    assert 'createElement("div")' not in render


def test_a_stalled_sync_is_reported_not_left_looking_slow():
    """`/api/sync/now` failing was swallowed whole, so the bar sat at 30% for
    150 seconds saying "Waiting for your first source to answer…"."""
    assert "function kickSync" in SRC
    assert "Couldn't start the sync" in SRC
    assert "showWarn" in SRC


# ── one accent, on the page the user meets first ───────────────────────────
#: From `styles.css`: "Blue is deliberately gone: it was the generic-AI default
#: the brief exists to avoid." It was still alive on this page, which is the
#: first thing anybody sees — so the seam ran the other way round.
BANNED = [
    "#6ea8fe", "110,168,254", "#3b82f6", "#60a5fa", "#7ec0ff", "#3f7fe0",
    "#5b9bff", "#a9d3ff", "#8fc0ff", "#b498f0", "#10b981", "#34d399",
    "#f59e0b", "#fbbf24", "#9ca3af", "#e5e7eb", "#d1d5db",
]


@pytest.mark.parametrize("literal", BANNED)
def test_no_off_palette_literals_in_the_onboarding(literal):
    assert literal not in SRC, (
        f"{literal} is in onboarding.html — use a token from :root instead")


def test_the_onboarding_has_no_blue_accent_left():
    assert "--blue" not in SRC, (
        "`--blue` is back; the one accent is `--north` (docs/DESIGN-BRIEF.md)")


def test_gold_is_the_accent_on_both_sides_of_the_seam():
    """`test_frontend_design_tokens.py` pins --ground/--star/--north to the same
    values in both files. That is necessary and it is not sufficient: the
    onboarding declared gold and then used blue for every job gold does in the
    workspace, so the user walked a blue onboarding and landed somewhere gold.
    """
    gold = SRC.count("var(--north")
    assert gold >= 20, f"only {gold} uses of the accent on the whole page"


def test_no_dingbat_is_doing_an_icons_job():
    """`✉ ☏ ✎ ▤ ▲ ◆` were the last ones in the product. A dingbat is a colour
    font that ignores `currentColor`, so it can never take the accent — and
    these sat three inches from hand-inlined SVG on the same screen."""
    for glyph in "✉☏✎▤▲◆✓✕★☆●■":
        assert glyph not in CODE, f"{glyph!r} is doing an icon's job"


def test_the_page_draws_from_the_one_icon_set():
    assert '<script src="/static/core.js">' in SRC
    assert "data-ic" in SRC
    #: Every glyph the page asks for has to exist in `IC`, or it renders blank.
    for name in sorted(set(re.findall(r'data-ic="([a-z]+)"', SRC))
                       | set(re.findall(r'ic\("([a-z]+)"\)', SRC))):
        assert re.search(rf"^\s+{name}: _S\(", CORE, re.M), (
            f"onboarding asks for IC.{name}, which core.js does not define")


# ── motion, on the most motion-heavy screen in the product ─────────────────
def test_reduced_motion_is_respected_by_the_first_screen_too():
    """`test_frontend_design_tokens.py` enforces this for `styles.css` and for
    the app's JS. It never read this file — which runs a canvas rAF loop
    redrawing a rotating point cloud every 32ms, plus a scanline overlay, a
    ripple and a horizontal glitch offset, and is the FIRST thing a new user
    sees. There were zero occurrences of `prefers-reduced-motion` in it."""
    assert "@media (prefers-reduced-motion: reduce)" in SRC
    #: The canvas is a rAF loop CSS cannot reach, so the JS has to ask as well.
    assert "LESS_MOTION" in SRC
    assert "prefers-reduced-motion" in SRC[SRC.index("<script>"):]


def test_nothing_animates_itself_under_reduced_motion():
    """Rotation, ripple and glitch are each started by the loop, not by CSS."""
    step = SRC[SRC.index("function step()"):SRC.index("function loop(t)")]
    guarded = step[step.index("if(LESS_MOTION){"):step.index("return;")]
    assert "ripple.on=false" in guarded
    assert "glitch=0" in guarded
    #: and the camera snaps rather than easing — an eased move is still motion.
    assert "ccx=tcx" in guarded


def test_the_still_frame_is_still_drawn():
    """Reduced motion is not a blank canvas: the field is a static image, so it
    is redrawn when the state it depends on changes."""
    assert "function redraw()" in SRC
    assert "needsDraw" in SRC


# ── the rest of the page ───────────────────────────────────────────────────
def test_the_title_is_in_the_head():
    head = SRC[SRC.index("<head>"):SRC.index("</head>")]
    assert "<title>" in head
    assert SRC.count("<title>") == 1


def test_the_hero_does_not_claim_something_the_screen_cannot_show():
    """It read "These are your four strongest areas right now" — on a screen
    showing no areas, beside a counter reading 0. That is the first sentence a
    new user reads, and it was false."""
    hero = SRC[SRC.index('<div class="hero"'):SRC.index('<div class="stats"')]
    copy = re.search(r'<p class="sub">(.*?)</p>', hero, re.S).group(1)
    assert "four strongest areas" not in copy, copy
    #: …and it does not promise anything else the hero cannot show either.
    assert "your four" not in copy.lower()


def test_the_progress_bar_reports_itself_to_assistive_tech():
    assert 'role="progressbar"' in SRC
    assert 'aria-valuenow' in SRC
    #: and the detail line is announced as it changes, because for anyone who
    #: cannot see the bar that line IS the progress.
    assert 'aria-live="polite"' in SRC


def test_the_modal_can_be_escaped_and_cannot_be_tabbed_out_of():
    assert 'role="dialog"' in SRC and 'aria-modal="true"' in SRC
    assert 'e.key==="Escape"' in SRC
    assert 'e.key!=="Tab"' in SRC


def test_focus_is_visible_on_a_dark_ground():
    """Every control here is a custom-painted button on charcoal, where the UA
    focus ring is very nearly invisible."""
    assert ":focus-visible{outline:2px solid var(--north)" in SRC


def test_the_build_and_digest_screens_adapt_to_the_smallest_window():
    """`desktop.py` sets `min_size=(920, 620)`. Only `.conn` had a media query,
    so at that size the four corner cards and the Continue button below them
    overlapped."""
    assert "@media(max-width:1180px),(max-height:820px)" in SRC
    small = SRC[SRC.index("@media(max-width:1180px),(max-height:820px)"):]
    small = small[:small.index("@media(min-width:1181px)")]
    for sel in (".dcard", ".dcont", ".bProg", ".bStats"):
        assert sel in small, f"{sel} does not adapt to a small window"


def test_nothing_is_swallowed_without_saying_what_it_was_attempting():
    """`except Exception: pass` is invisible afterwards, in this language too."""
    js = SRC[SRC.index("// ── backend wiring"):]
    #: A deliberate no-op is spelled `catch(_){/* why */}` — the JS counterpart
    #: of `suppressed("what you were attempting")`. An EMPTY one says nothing
    #: to the next reader and nothing to the next person debugging it.
    bare = re.findall(r"catch\s*\(\s*\w*\s*\)\s*\{\s*\}", js)
    assert not bare, f"{len(bare)} silent catches in the onboarding's wiring"
    #: and the build loop's own polls reach the screen when they stop answering.
    assert "LOST_AFTER" in js and "Lost contact" in js


# ── the second onboarding that was still in the workspace ──────────────────
def test_there_is_exactly_one_onboarding():
    """`workspace.js` still bound a `#onboard` modal with six buttons, kept
    alive by six empty `hidden` stubs in index.html so the bindings would not
    throw. `openOnboard()` had no caller, and what it offered was stale."""
    ws = (WEB / "workspace.js").read_text()
    index = (WEB / "index.html").read_text()
    for dead in ("openOnboard", "obSkip", "obDone", "obGoogle", "obFact"):
        assert f'"#{dead}"' not in ws and f'id="{dead}"' not in index, dead


# ── what the EYES see, which `inert` does not govern ────────────────────────

def test_the_stage_says_when_the_account_screen_is_up():
    """Every pane's visibility is a CSS rule keyed on a `.stage` class.

    `setLive` only sets `inert`, which takes a pane out of the tab order and
    leaves it **on screen** — so without this class the account screen and the
    hero drew stacked on top of each other, and every harness stayed green
    because a fake DOM has no opacity.
    """
    assert "account" in panes([], account_done=False)["stageClasses"]
    assert "account" not in panes([], account_done=True)["stageClasses"]
    assert "account" not in panes(["on"])["stageClasses"]


def test_the_account_screen_does_not_share_the_heros_class():
    """The bug itself, as a source check.

    `.hero` is visible whenever the stage lacks `.on`, so two elements with that
    class are two screens drawn at once. This is the one assertion that would
    have caught it.
    """
    page = PAGE.read_text()
    start = page.index('id="paneAccount"')
    tag = page[page.rindex("<", 0, start):page.index(">", start) + 1]
    assert 'class="acct"' in tag, f"paneAccount's class changed: {tag}"
    assert 'class="hero"' not in tag


def test_the_hero_is_hidden_by_css_while_the_account_screen_is_up():
    """And its furniture with it — a counter reading "0 memories" belongs to the
    screen about building a brain, not to the one asking who you are."""
    page = PAGE.read_text()
    assert ".stage.account .acct{opacity:1" in page, \
        "the account screen has no rule making it visible"
    for hidden in (".stage.account .hero", ".stage.account .stats",
                   ".stage.account .bot"):
        assert hidden in page, f"{hidden} is not hidden during the account step"
