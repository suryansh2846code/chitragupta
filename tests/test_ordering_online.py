"""An agent fills a basket freely and never spends money on its own.

The capability splits in two and the split is the whole design. **Filling** —
searching, opening a listing, adding to a basket — is an ordinary click on a
site the user allowed changes on, and it has to stay that cheap or nobody uses
this twice. **Committing** is pulled back out of the per-site decision: a user
who said "yes, act on this shop" did not thereby agree to a total they have
never seen, so the order button is refused at the tool and goes to a card.

What these tests watch for, in the order the failures actually happened:

* an agent telling the user it cannot buy anything when it can — the screenshot
  that started this was the model quoting a tool description that had been left
  behind by the write tools shipping
* the gate reading the model's word for a control instead of the page's
* a basket that moved between the card being drawn and the button being pressed
* the gate being written once per phrase instead of once per shape
"""
from __future__ import annotations

import pytest

from chitragupta import actions
from chitragupta.action_phrasing import describe
from chitragupta.agents import browse_tools, library, permissions, prompt, tools
from chitragupta.browser import live, origins, purchase
from chitragupta.browser.driver import CHANGING_ACTS
from chitragupta.browser.page import Node
from chitragupta.browser.session import Session

SHOP = "https://shop.example/basket"

BASKET = [
    Node(role="heading", name="Your basket"),
    Node(role="text", name="Rolled oats 1kg  ₹249"),
    Node(role="text", name="Order total ₹2,480"),
    Node(role="text", name="Payment method: UPI"),
    Node(role="button", name="Add to cart", handle="button␟Add to cart"),
    Node(role="textbox", name="Search", handle="textbox␟Search"),
    Node(role="textbox", name="Card number", handle="textbox␟Card number"),
    Node(role="button", name="Place your order", handle="button␟Place your order"),
]


class _Acts:
    """A page that records what was pressed, and can be swapped underneath."""

    def __init__(self, nodes=None, url=SHOP):
        self.nodes = list(nodes if nodes is not None else BASKET)
        self.url = url
        self.acted: list[tuple[str, str, str]] = []

    def _page(self):
        return self.url, "Basket", list(self.nodes)

    def goto(self, url):
        self.url = url
        return self._page()

    def current(self):
        return self._page()

    def back(self):
        return self._page()

    def act(self, kind, handle, text=""):
        self.acted.append((kind, handle, text))
        return self._page()

    def close(self):
        pass


@pytest.fixture
def shop():
    conn = origins._conn()
    conn.execute("DELETE FROM browser_origins")
    conn.commit()
    driver = _Acts()
    browse_tools.set_session(Session(driver))
    origins.grant("shop.example", may_act=True)
    browse_tools.browse_open(SHOP)
    yield driver
    browse_tools.set_session(None)


def _ref(name: str) -> str:
    snap = browse_tools.get_session().snapshot
    return next(r for r, n in snap.refs.items() if n.name == name)


# ── filling a basket stays free ─────────────────────────────────────────────
def test_adding_to_a_basket_needs_no_approval(shop):
    """The half that makes this worth having. An agent that needs a tap per bag
    of oats is an agent the user does the shopping for."""
    out = browse_tools.browse_click(ref=_ref("Add to cart"), label="Add to cart")

    assert out.ok is True
    assert shop.acted == [("click", "button␟Add to cart", "")]


def test_searching_is_still_typing_like_any_other(shop):
    out = browse_tools.browse_type("rolled oats", ref=_ref("Search"),
                                   label="Search")

    assert out.ok is True
    assert shop.acted == [("type", "textbox␟Search", "rolled oats")]


@pytest.mark.parametrize("label", list(purchase.NOT_A_PURCHASE))
def test_the_controls_a_basket_is_built_with_are_never_treated_as_purchases(label):
    """Parametrised over the list rather than over the one in the screenshot:
    "Add to cart" and "Proceed to checkout" fail the same way, and a test that
    named one would have let the other through."""
    assert purchase.commits_a_purchase(label) is False
    assert purchase.commits_a_purchase(label, at_checkout=True) is False


# ── committing never is ─────────────────────────────────────────────────────
def test_the_order_button_is_refused_and_nothing_is_pressed(shop):
    out = browse_tools.browse_click(ref=_ref("Place your order"),
                                    label="Place your order")

    assert out.ok is False
    assert shop.acted == [], "an agent placed an order on its own"


def test_the_refusal_sends_the_agent_to_the_card_not_to_a_setting(shop):
    """A permission-shaped refusal makes an agent hunt for a setting to ask the
    user for, and there is none — the tap IS the mechanism."""
    out = browse_tools.browse_click(ref=_ref("Place your order"),
                                    label="Place your order")

    assert "place_order" in str(out)
    assert "permission" not in str(out).lower()


@pytest.mark.parametrize("label", [
    "Place your order", "Place order", "Buy now", "Buy it now", "Pay now",
    "Confirm and pay", "Complete purchase", "Confirm your order",
    "Submit order", "Proceed to pay", "Start subscription", "Book now",
    "Pay ₹2,480", "Order now",
])
def test_every_way_a_shop_spells_the_order_button_is_refused(label, shop):
    """The shape, not the instance. Amazon says "Place your order", a
    subscription says "Start subscription", and a checkout that shortens it to
    the price says "Pay ₹2,480" — one gate, every spelling."""
    shop.nodes = [*BASKET, Node(role="button", name=label, handle=f"b␟{label}")]
    browse_tools.browse_read()

    out = browse_tools.browse_click(ref=_ref(label), label=label)

    assert out.ok is False, f"{label!r} was pressed"
    assert shop.acted == []


def test_a_bare_confirm_commits_only_where_the_page_is_already_a_checkout():
    """Two factors, and the second only widens the first. "Confirm" on a
    settings page is not a purchase; on a page showing a total and a way to pay
    it is the button that charges the card."""
    assert purchase.commits_a_purchase("Confirm") is False
    assert purchase.commits_a_purchase("Confirm", at_checkout=True) is True


def test_stepping_through_a_checkout_is_not_four_cards():
    """`/CLAUDE.md`: a tap nobody reads by the fourth time is not consent. The
    navigation words stay out of the checkout list for exactly that reason."""
    assert purchase.commits_a_purchase("Continue", at_checkout=True) is False
    assert purchase.commits_a_purchase("Next", at_checkout=True) is False


def test_the_gate_reads_the_page_not_the_agents_word_for_the_control(shop):
    """The injection shape. A model — or page text the model just read — naming
    the order button "Continue" must not be what the gate believes."""
    out = browse_tools.browse_click(ref=_ref("Place your order"),
                                    label="Continue")

    assert out.ok is False
    assert shop.acted == []


def test_submitting_a_form_cannot_route_around_the_click_gate(shop):
    """Several tools change a page and only one of them was clicking. A gate on
    `browse_click` alone is a gate with doors beside it."""
    out = browse_tools.browse_submit(ref=_ref("Place your order"),
                                     label="Place your order")

    assert out.ok is False
    assert shop.acted == []


def test_pressing_a_key_on_the_order_button_is_refused_too(shop):
    """`browse_press` arrived after this gate did, which is exactly the case it
    has to survive: *Enter* on a focused "Place your order" is the same event as
    clicking it, and a gate that named `click` and `submit` by hand would have
    gained a hole the moment a verb was added."""
    out = browse_tools.browse_press("Enter", ref=_ref("Place your order"),
                                    label="Place your order")

    assert out.ok is False
    assert shop.acted == []


#: Every changing verb, driven rather than named. A first version of the test
#: below read `_may_spend`'s source for the string "CHANGING_ACTS" and passed
#: while `press` was walking straight through the gate — the import line
#: mentioned it. Driving each verb is the only version that can see the hole.
def _drive(kind: str, ref: str, label: str):
    if kind == "type":
        return browse_tools.browse_type("x", ref=ref, label=label)
    if kind == "select":
        return browse_tools.browse_select("x", ref=ref, label=label)
    if kind == "press":
        return browse_tools.browse_press("Enter", ref=ref, label=label)
    if kind == "submit":
        return browse_tools.browse_submit(ref=ref, label=label)
    return browse_tools.browse_click(ref=ref, label=label)


@pytest.mark.parametrize("kind", sorted(CHANGING_ACTS))
def test_every_verb_that_changes_a_page_passes_the_purchase_gate(kind, shop):
    """The shape, driven against the table the verbs are declared in.

    `driver.CHANGING_ACTS` is what `_gate_for` already derives the unattended
    floor from, and this gate reads the same set — so a verb added tomorrow is
    covered the day it exists rather than the day somebody remembers. This test
    goes red for a new verb that nobody taught `_drive`, which is the right
    way round: the reminder arrives with the verb.
    """
    out = _drive(kind, _ref("Place your order"), "Place your order")

    assert out.ok is False, f"{kind} reached the order button"
    assert shop.acted == []


def test_scrolling_the_order_button_into_view_is_still_free(shop):
    """`reveal` is a read: it sends nothing and presses nothing. An agent that
    could not scroll to the thing it is about to tell the user about would
    report a basket it cannot see."""
    out = browse_tools.browse_reveal(ref=_ref("Place your order"),
                                     label="Place your order")

    assert out.ok is True


# ── secrets the agent must never hold ───────────────────────────────────────
def test_a_card_number_box_is_refused_whatever_the_site_is_allowed(shop):
    out = browse_tools.browse_type("4111111111111111", ref=_ref("Card number"),
                                   label="Card number")

    assert out.ok is False
    assert shop.acted == []


def test_the_agent_is_told_never_to_ask_the_user_for_the_number(shop):
    """Refusing to type one and then asking the user to paste it into the chat
    is the same secret in a worse place."""
    out = browse_tools.browse_type("4111", ref=_ref("Card number"),
                                   label="Card number")

    assert "never ask the user" in str(out).lower()


@pytest.mark.parametrize("label", [
    "Card number", "CVV", "Security code", "Expiry date", "Name on card",
    "UPI PIN", "Password", "OTP", "One-time password", "Verification code",
])
def test_every_kind_of_secret_is_refused_not_only_a_card(label):
    assert purchase.asks_for_a_secret(label) is True


def test_an_ordinary_box_is_not_mistaken_for_a_secret():
    for label in ("Search", "Delivery address", "Flat, house no.", "Pincode"):
        assert purchase.asks_for_a_secret(label) is False


# ── the action: never unattended, never promotable ──────────────────────────
def test_placing_an_order_can_never_run_unattended():
    """A routine reads text strangers wrote. There is no allow-list entry that
    makes buying something on that basis acceptable."""
    assert actions.REGISTRY["place_order"].risk is actions.Risk.RED
    assert "place_order" in permissions.NEVER_UNATTENDED


def test_placing_an_order_offers_no_undo_button():
    """We cannot un-buy anything, and a button that quietly does nothing is
    worse than no button."""
    spec = actions.REGISTRY["place_order"]
    assert spec.undo is None
    assert spec.public()["reversible"] is False


def test_the_card_says_why_it_always_asks():
    assert "approval" in actions.REGISTRY["place_order"].always_ask_because


def test_an_order_is_described_by_its_total(shop):
    """The log row is read when somebody is checking a bank statement against
    it. "Place an order" tells them nothing."""
    line = describe("place_order", {
        "items": [{"name": "oats", "qty": 2}], "site": "shop.example",
        "total": "₹2,480"})

    assert "₹2,480" in line and "shop.example" in line


# ── the action: what runs is what the page still says ───────────────────────
def _order(**extra):
    params = {"items": [{"name": "Rolled oats 1kg", "qty": 1, "price": "₹249"}],
              "total": "₹2,480", "control": "Place your order",
              "site": "shop.example"}
    params.update(extra)
    return actions.REGISTRY["place_order"].handler(params)

def test_an_approved_order_presses_the_button(shop):
    out = _order(ref=_ref("Place your order"))

    assert out["ok"] is True
    assert shop.acted == [("click", "button␟Place your order", "")]


def test_an_order_is_refused_when_the_basket_has_moved_underneath_it(shop):
    """The check the card is worth having. A price changes, delivery lands,
    something goes out of stock — the user approved ₹2,480, not whatever the
    page says by the time they press Confirm."""
    ref = _ref("Place your order")
    shop.nodes = [n if "Order total" not in n.name
                  else Node(role="text", name="Order total ₹3,990")
                  for n in shop.nodes]

    out = _order(ref=ref)

    assert out["ok"] is False
    assert "₹2,480" in out["error"]
    assert shop.acted == [], "it bought the new total"


def test_an_order_with_no_browser_open_says_so_rather_than_starting_one(shop):
    """Opening a fresh browser here would find no basket in it, minutes after
    the page the card describes stopped existing."""
    browse_tools.set_session(None)

    out = _order()

    assert out["ok"] is False
    assert "no longer" in out["error"]
    assert live.current() is None, "it launched a browser to place an order"


def test_an_order_still_goes_through_the_sites_own_permission(shop):
    """The card is a gate ABOVE the browser's, never instead of it. A site the
    user has un-allowed since the card was drawn is still refused."""
    origins.grant("shop.example", may_act=False)

    out = _order(ref=_ref("Place your order"))

    assert out["ok"] is False
    assert shop.acted == []


def test_an_order_refuses_rather_than_guessing_at_a_missing_total(shop):
    out = _order(total="", ref=_ref("Place your order"))

    assert out["ok"] is False
    assert shop.acted == []


# ── and the thing the screenshot was actually about ─────────────────────────
def test_no_tool_tells_an_agent_it_cannot_buy_anything():
    """The regression. `browse_open` said *"you can read and report; you cannot
    click, type or buy anything"* for as long as the write tools had existed,
    so an agent asked to order groceries quoted it back — *"that's a hard
    limit, not a setting"* — and handed the user a list to paste in themselves.

    Checked over every tool rather than the one that had it: the sentence was
    copied, and the copy is always the one that gets read."""
    for name, tool in tools.TOOL_DEFS.items():
        low = tool.description.lower()
        for claim in ("cannot click", "cannot buy", "cannot type",
                      "or buy anything", "can only read"):
            assert claim not in low, f"{name} still says “{claim}”"


def test_an_agent_that_can_browse_and_buy_is_told_how_the_two_compose():
    """Having the tools is not knowing they compose. Without the recipe the
    model decides the capability does not exist and writes out a shopping list
    for the user to retype, which is what it did."""
    text = prompt.build(
        name="Shopping", role="orders", system_prompt="", agent_id="shopping",
        tools=["browse_open", "browse_click", "browse_find"],
        actions=["place_order"])

    assert "place_order" in text
    assert "browse_click" in text
    assert "do not paste a shopping list" in text.lower()


def test_an_agent_with_no_order_action_is_not_taught_the_recipe():
    """It would be told to propose something nothing will run."""
    text = prompt.build(
        name="Statements", role="bills", system_prompt="",
        agent_id="statements",
        tools=["browse_open", "browse_click"], actions=["set_reminder"])

    assert "place_order" not in text


def test_the_shopping_agent_can_reach_a_page_and_place_an_order():
    """A template that could propose an order and not open a shop would be a
    control that cannot work."""
    shopping = library.BY_ID["shopping"]

    assert "place_order" in shopping.actions
    assert "browse_click" in shopping.tools
    assert "browser" in shopping.needs


def test_the_agent_in_the_screenshot_can_order_the_food_it_planned():
    """Health & Fitness plans the meals, writes the shopping list, and was the
    agent asked to order it."""
    assert "place_order" in library.BY_ID["health"].actions
