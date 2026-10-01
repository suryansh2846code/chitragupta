"""What on a page spends the user's money, and what asks for their card.

An agent that can click can buy, and the moment that is true every other gate
in this package is answering a question that is no longer the whole question.
`origins.may_act` is a decision about a **site** — *"yes, act on amazon.in"* —
taken once, deliberately, on the Connectors screen. It is the right unit for
opening a chat, typing a message, ticking a box. It is not a decision about
**this basket at this total**, which is a thing a person can only agree to
having seen it.

So acting on a site stays one decision per site, and spending money is pulled
back out of it: a control that commits a purchase is refused at the tool, and
the order goes through `place_order` — an action, RED, one card showing the
basket and the total, every single time. `docs/ACTION-COVERAGE.md` listed
`make_purchase` in red from the beginning; this is the module that makes that
true rather than aspirational.

**Why reading the control's own name is sound here**, when a label heuristic
usually is not: a shop is required to say what its commit button does. "Place
your order", "Pay now", "Buy now", "Confirm and pay" — the unambiguous wording
is a legal obligation in most of the places this ships to, not a design fashion
that changes with a redesign. A button that spends money and says "Continue"
instead is the rare case, and the second factor below is what catches it.

**Two factors, and the second only widens the first.** On any page, the
unmistakable verbs commit. On a page that is plainly the last step of a
checkout — it shows a total *and* a way to pay — the bare words commit too
("Confirm", "Submit", "Place", "Pay"). Navigation words are deliberately NOT in
that second list: an agent stepping through address → delivery → payment presses
"Continue" three times, and a card on each is the failure `/CLAUDE.md` already
names — *"a tap nobody reads by the fourth time is not consent"*.

**The asymmetry is the whole design.** A control we stop that was not a purchase
costs one card the user reads once. A control we let through that was costs them
money they did not agree to spend. Every judgement call in this file is made in
that direction, and a phrase added here needs no stronger argument than "it
might buy something".

Nothing here decides anything about permission; it recognises. The refusal is in
`agents/browse_tools.py` and the gate it feeds is `origins.may_act`'s neighbour,
not its replacement — a site nobody allowed is still refused before any of this
is asked.
"""
from __future__ import annotations

import re

#: Phrases that commit a purchase **wherever they appear**.
#:
#: Multi-word on purpose. A bare "buy" matches *Buying guide* and *Best buys*,
#: and a gate that fires on a magazine link is a gate the user learns to ignore.
#: Each of these is a sentence a shop writes on the one button that charges a
#: card, because it is required to.
BUYS = (
    # The order itself.
    "place order", "place your order", "place the order", "placeorder",
    "order now", "submit order", "submit your order", "complete order",
    "complete your order", "confirm order", "confirm your order",
    "finish order", "finish your order", "review and place",
    # Buying one thing outright.
    "buy now", "buy it now", "buy with", "buy this", "one-click order",
    "1-click order", "1 click order",
    # Paying.
    "pay now", "pay and confirm", "confirm and pay", "pay securely",
    "make payment", "make the payment", "complete payment",
    "complete the payment", "proceed to pay", "pay for this", "pay with",
    "authorise payment", "authorize payment",
    # Purchases that are not called orders.
    "complete purchase", "confirm purchase", "purchase now",
    "start subscription", "subscribe and pay", "confirm subscription",
    "place bid", "place your bid", "confirm bid",
    "book now", "confirm booking", "reserve and pay", "rent now",
)

#: Words that commit **only on the last step of a checkout**, where the page has
#: already shown what is being bought and how it will be paid for.
#:
#: "Continue" and "Next" are deliberately absent. They are how somebody moves
#: between the steps of a checkout, and a card on each of those is three taps
#: that teach the fourth one away — which is the one that matters.
COMMITS_AT_CHECKOUT = (
    "confirm", "submit", "place", "pay", "complete",
    "authorise", "authorize", "subscribe", "checkout now",
)

#: Deliberately NOT a purchase, and tested as such. Filling a basket is the
#: whole of what makes this capability worth having — an agent that needs a tap
#: to add a bag of oats is an agent nobody will use twice.
NOT_A_PURCHASE = (
    "add to cart", "add to basket", "add to bag", "add to list",
    "proceed to checkout", "go to cart", "view cart", "checkout",
    "continue", "next", "apply coupon", "save for later",
)

#: What a page says when it is the last step of a checkout. One sign from each
#: group is required: a total alone is a basket, a payment word alone is a
#: settings page, and the two together are somebody about to be charged.
SHOWS_A_TOTAL = (
    "order total", "grand total", "total amount", "amount payable",
    "amount to pay", "order summary", "you pay", "total payable",
    "subtotal", "total:",
)
SHOWS_A_WAY_TO_PAY = (
    "payment method", "payment options", "card number", "credit card",
    "debit card", "cvv", "upi", "net banking", "billing address",
    "gift card", "wallet", "cash on delivery",
)

#: Addresses that are a checkout even before the page is read.
CHECKOUT_PATHS = ("/checkout", "/payment", "/gp/buy", "/buy/", "/pay/",
                  "/placeorder", "/order/confirm", "/cart/checkout")

#: Boxes an agent must never type into, whatever the site is allowed to do.
#:
#: Two different reasons, same answer. A card number, a CVV or a UPI PIN is a
#: secret we do not have and must never carry — the user's saved payment method
#: is the only honest way through a checkout, and an agent that asks them to
#: paste a card number into a chat has taught them to do that somewhere else
#: too. A password or a one-time code is the same fact about a different
#: secret: it is the thing that proves the person is there, so an agent typing
#: one is an agent defeating the check the user relies on.
TAKES_A_SECRET = (
    "card number", "cardnumber", "card no", "credit card number",
    "debit card number", "cvv", "cvc", "cvv2", "security code",
    "expiry", "expiration", "exp date", "name on card", "cardholder",
    "card holder", "upi pin", "atm pin", "card pin",
    "password", "passcode", "otp", "one-time", "one time password",
    "verification code", "authentication code", "2fa", "two-factor",
)


def _plain(text: str) -> str:
    """A control's name, flattened so a phrase can be looked for in it.

    Punctuation becomes a space rather than vanishing: "Place&nbsp;order." and
    "Place order" must read the same, while "Buy now" must not be found inside
    "Buynowhere". Collapsing to single spaces is what lets every phrase above be
    written the way a person would say it.
    """
    low = (text or "").lower().replace(" ", " ")
    return " ".join(re.sub(r"[^a-z0-9₹$€£.,/-]+", " ", low).split())


def _has_word(haystack: str, word: str) -> bool:
    """A whole word, not a fragment — "pay" must not match "payslips"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])",
                     haystack) is not None


def is_payment_page(url: str = "", text: str = "") -> bool:
    """Is this page the last step, where pressing something charges a card?"""
    low = _plain(text)
    total = any(sign in low for sign in SHOWS_A_TOTAL)
    pays = any(sign in low for sign in SHOWS_A_WAY_TO_PAY)
    if total and pays:
        return True
    # An address that only a checkout has, plus either half. The URL alone is
    # not enough: `/checkout` is also where a basket is reviewed, and gating
    # every click there is the three-taps-then-four failure above.
    path = (url or "").lower()
    return (total or pays) and any(p in path for p in CHECKOUT_PATHS)


def commits_a_purchase(name: str, *, at_checkout: bool = False) -> bool:
    """Would pressing this spend the user's money?

    `name` is the accessible name the **page** gave the control, taken off the
    snapshot — never the model's description of it. A page that can name its own
    buttons can already name them anything; what it must not get is a second,
    softer name supplied by whatever just read the page.
    """
    flat = _plain(name)
    if not flat:
        return False
    if any(phrase in flat for phrase in NOT_A_PURCHASE) and not any(
            phrase in flat for phrase in BUYS):
        # Said first and said explicitly. "Proceed to checkout" contains
        # "checkout" and "Add to cart" contains nothing dangerous at all, and
        # both are the controls this capability exists to press freely.
        return False
    if any(phrase in flat for phrase in BUYS):
        return True
    bare = any(_has_word(flat, word) for word in COMMITS_AT_CHECKOUT)
    if at_checkout and bare:
        return True
    # **A control that names a price is a control that charges it.** "Pay
    # ₹2,480" is a commit wherever it appears — the amount is doing the work the
    # verb list would otherwise have to guess at. A price with no verb at all
    # ("₹2,480 ›", which is how several shops render the final button) is only
    # read that way on a checkout, where a price on a button cannot be a
    # product listing.
    money = bool(_MONEY.search(name or ""))
    return money and (bare or at_checkout)


def asks_for_a_secret(name: str) -> bool:
    """Is this box asking for something only the user may type?"""
    flat = _plain(name)
    return bool(flat) and any(phrase in flat for phrase in TAKES_A_SECRET)


#: A money-looking run of digits, with or without a symbol in front of it.
_MONEY = re.compile(r"[₹$€£]\s*\d|(?<![\w.])\d[\d,  ]*\.\d{2}(?![\d])")

#: Every number on a page that could be an amount.
_AMOUNT = re.compile(r"\d[\d,  ]*(?:\.\d{1,2})?")


def normalise_amount(raw: str) -> str:
    """"₹2,480.00" and "2480" become the same string, or "" for a non-amount.

    Trailing zero paise are dropped, because a card showing "₹2,480" and a page
    showing "₹2,480.00" are the same number and refusing the order over it would
    be the app failing a check it invented.
    """
    found = _AMOUNT.search(str(raw or ""))
    if not found:
        return ""
    body = found.group(0).replace(",", "").replace(" ", "").replace(" ", "")
    if "." in body:
        whole, _, part = body.partition(".")
        part = part.rstrip("0")
        return f"{whole}.{part}" if part else whole
    return body


def amounts(text: str) -> set[str]:
    """Every amount on a page, normalised the same way."""
    return {normalise_amount(m.group(0)) for m in _AMOUNT.finditer(text or "")} - {""}


def page_still_says(total: str, text: str) -> bool:
    """Is the total the user approved still the total on the page?

    The card is a promise about a page that was read minutes ago. A basket can
    change underneath it — a price moves, a delivery charge lands, an item goes
    out of stock and the shop quietly swaps it — and the approval was for the
    number the user saw, not for whatever the page says at the moment the button
    is pressed. So the handler re-reads and asks this, and a disagreement
    refuses the order instead of buying the new number.
    """
    want = normalise_amount(total)
    return bool(want) and want in amounts(text)
