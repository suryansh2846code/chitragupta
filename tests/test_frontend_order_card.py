"""The card somebody reads before money leaves their account.

Every other confirmation card shows something the agent **wrote** — an email, an
event, a session — and the user's job is to fix what was misread. This one shows
something the agent **read**: the items, the prices and the total are the shop's
numbers, copied off a basket page. That inverts what the card is for, and two
things follow from it.

The rows have to name every item with its quantity and its price, because the
decision being taken is "is this the right basket", and a card that summarised
it as "10 items — ₹2,480" would be asking to be trusted rather than read. And
nothing on it may be typeable: a box around the total would let somebody edit a
figure that changes the card and not the charge, which is the one lie a card may
never tell.

Executed rather than asserted on the source, for the reason `tests/js/` exists:
the registry fallback renders *any* unknown action as a label and a line of
JSON, so an order card that had silently fallen through to it would pass every
check that reads the branch chain and show the user `[{"name":"Rolled oats…`.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

ORDER = {
    "type": "place_order",
    "params": {
        "site": "shop.example",
        "total": "₹2,480",
        "control": "Place your order",
        "items": [
            {"name": "Rolled oats 1kg", "qty": 1, "price": "₹249"},
            {"name": "Peanut butter 500g", "qty": 2, "price": "₹398"},
        ],
    },
}

#: What `/api/actions/catalog` publishes for this action. Seeded explicitly so
#: the card is rendered the way the real app renders it — the risk tier and the
#: always-ask sentence both come from here.
CATALOG = {
    "place_order": {
        "label": "Place an order",
        "fields": ["site", "items", "total", "control"],
        "risk": "red",
        "reversible": False,
        "required": ["items", "total", "control"],
        "always_ask_because": (
            "Spending your money always needs your approval — every time, and "
            "there is no setting that changes it."),
        "depends_on": {}, "identity": ["total", "control"],
        "only_when_set": [], "undo_label": "", "connector": "", "capability": "",
    },
}


def _run(action, result, **extra):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card_plain.mjs"), str(WEB / "app.js")],
        input=json.dumps({"action": action, "result": result,
                          "catalog": CATALOG, **extra}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _parse(text):
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/action_card.mjs"), str(WEB / "app.js")],
        input=json.dumps({"text": text, "catalog": CATALOG}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def card():
    return _run(ORDER, {"ok": True, "detail": "Ordered 2 item(s) — ₹2,480"})


# ── it is an order card, not the registry fallback ──────────────────────────
def test_it_rendered(card):
    assert card["error"] is None, card["error"]


def test_it_says_what_is_being_bought_and_where(card):
    assert "Order 3 items" in card["text"], card["text"]
    assert "shop.example" in card["text"]


def test_it_did_not_fall_through_to_the_registry_fallback(card):
    """The trap `mcp_action` hit once and eight later actions hit again: an
    action the branch chain has not been taught about renders its parameters as
    JSON, and the user is asked to approve a line of it."""
    # Checked in the ESCAPED form the card actually writes. The first version
    # of this assertion looked for `{"name"` and passed while the card was
    # showing `[{&quot;name&quot;:&quot;Rolled oats 1kg&quot;…` — a test that
    # could not see the failure it was written for, which is the whole reason
    # the rule is to watch one go red before trusting it.
    assert "&quot;name&quot;" not in card["html"], card["html"][:200]
    assert '{"name"' not in card["html"]


# ── every item, named ───────────────────────────────────────────────────────
def test_every_item_is_on_the_card_with_its_price(card):
    for fragment in ("Rolled oats 1kg", "₹249", "Peanut butter 500g", "₹398"):
        assert fragment in card["text"], fragment


def test_a_quantity_above_one_is_shown(card):
    """Two jars arriving is not the same decision as one, and the quantity is
    the half of it that is easiest to misread."""
    assert "Peanut butter 500g × 2" in card["text"]


def test_the_total_is_the_shops_and_is_on_the_card(card):
    assert "₹2,480" in card["text"]


def test_the_card_says_the_total_is_checked_again_at_confirm(card):
    """The behaviour that makes an old card safe to leave sitting there. If the
    user does not know it, a basket that moved looks like the app refusing for
    no reason."""
    assert "nothing is ordered" in card["text"].lower()


# ── nothing on it is typeable ───────────────────────────────────────────────
def test_nothing_on_an_order_card_can_be_typed_into(card):
    """A readback, not a draft. An edited total would change what the card says
    and not what the shop charges — and the handler re-reads the page at
    Confirm, so an edited one could only ever block the order it describes."""
    assert card["fieldCount"] == 0, card["editableFields"]


# ── the tier, in the user's words ───────────────────────────────────────────
def test_it_is_a_red_card_and_says_why_it_always_asks(card):
    assert card["risk"] == "red"
    assert "every time" in card["text"]


def test_a_placed_order_offers_no_undo(card):
    """We cannot un-buy anything. A button that quietly did nothing would be
    the worst version of that."""
    assert "Undo" not in card["html"]


# ── the parser twin ─────────────────────────────────────────────────────────
def test_the_basket_is_read_out_of_the_tag_body():
    """Attributes are flat strings, so the items travel as JSON in the body —
    the same shape the server parses, because a card that disagreed with the
    server about what is in the basket would be approving something else."""
    out = _parse(
        'Here is the basket.\n'
        '<action type="place_order" site="shop.example" total="₹2,480" '
        'control="Place your order" ref="e31">'
        '{"items": [{"name": "Rolled oats 1kg", "qty": 1, "price": "₹249"}]}'
        '</action>')

    assert len(out["actions"]) == 1
    action = out["actions"][0]
    assert action["type"] == "place_order"
    assert action["params"]["items"][0]["name"] == "Rolled oats 1kg"
    assert action["params"]["total"] == "₹2,480"
    assert "<action" not in out["clean"]


def test_an_order_with_no_basket_draws_no_card_at_all():
    """Malformed JSON is dropped rather than guessed at, exactly as the server
    drops it. Half-parsing a basket and rendering the result is a card for
    something nobody proposed."""
    out = _parse('<action type="place_order" total="₹2,480" '
                 'control="Place your order">not json</action>')

    assert out["actions"] == []


def test_a_crafted_item_name_cannot_write_markup_into_the_card():
    """The items come off a shop's page, which makes every one of them text
    somebody else wrote — the same threat model as an email subject."""
    out = _run({"type": "place_order", "params": {
        "site": "shop.example", "total": "₹2,480",
        "control": "Place your order",
        "items": [{"name": "<img src=x onerror=alert(1)>", "qty": 1,
                   "price": "₹1"}]}},
        {"ok": True, "detail": "ok"})

    assert "<img" not in out["html"]
    assert "&lt;img" in out["html"]
