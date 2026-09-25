"""The Websites shelf — one card per site, and only one.

Connectors listed allowed sites as bare hostnames in a row of text. A person
scanning for "am I connected to LinkedIn" had to read addresses; a site they had
signed in to looked the same as one they had merely allowed. So each site got a
card with its mark.

**Then there were two of everything.** The card carried the mark, the account
and Disconnect; the old row underneath carried the address, the permission and
Remove — and both buttons called the same endpoint. LinkedIn appeared as
`LinkedIn` and again as `www.linkedin.com`, and which one you pressed decided
nothing. The row is gone and what only it carried moved onto the card: the
address, and whether agents may change things or only read.

The account is the part worth testing. A grant carries `{origin, host, may_read,
may_act, note}` and **no username**, so there is nothing to print for most sites
— and printing one we do not have would be worse than printing none.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"


def shelf(grants, probe=None) -> dict:
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/website_shelf.mjs"),
         str(WEB / "browser.js"), str(WEB / "connectors.js")],
        input=json.dumps({"grants": grants, "probe": probe or []}),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def sub(out: dict, label: str) -> str:
    return next(s for (n, s) in out["cards"] if n == label)


def test_the_shelf_lists_the_sites_people_ask_for():
    assert shelf([])["catalogue"] == ["LinkedIn", "WhatsApp", "X", "Discord", "Reddit"]


def test_every_card_carries_a_mark():
    """A logo is how this is scanned. A row of identical cards is a list again."""
    assert shelf([])["marks"] == 5


def test_an_unconnected_site_says_what_it_would_give_you():
    assert sub(shelf([]), "WhatsApp") == "Your chats, through WhatsApp Web."
    assert shelf([])["off"] == ["linkedin", "whatsapp", "x", "discord", "reddit"]


def test_a_connected_site_shows_the_account_when_there_is_one():
    out = shelf([{"host": "x.com", "note": "@suryansh"}])
    assert sub(out, "X") == "x.com · @suryansh"
    assert out["on"] == ["x.com"]


def test_a_connected_card_says_which_address_it_granted():
    """The one thing the deleted row carried that the card did not. "LinkedIn"
    alone does not say whether the grant is on linkedin.com or something that
    merely calls itself LinkedIn — and that is the whole unit of consent."""
    assert "www.linkedin.com" in sub(
        shelf([{"host": "www.linkedin.com", "note": ""}]), "LinkedIn")


def test_a_connected_site_with_no_account_says_signed_in_and_nothing_more():
    """A grant has no username. Inventing one is worse than saying none.

    `signin.py` always writes *some* note — the account where it has one, and
    "signed in from Connectors" where it does not — so a note is what proves a
    sign-in happened, and the account is the part that may be missing.
    """
    assert sub(shelf([{"host": "linkedin.com", "note": "signed in from Connectors"}]),
               "LinkedIn") == "linkedin.com · Signed in"


def test_a_site_only_allowed_does_not_claim_you_signed_in():
    """The card said "Signed in" to every grant it had. Typing an address into
    the box lets agents *read* a site; connecting one lets them read it AS
    YOU. Claiming the second when the user did the first is a thing they would
    only discover when they wondered why the agent could not see anything."""
    out = shelf([{"host": "payroll.example.com", "note": ""}])

    assert sub(out, "payroll.example.com") \
        == "payroll.example.com · Allowed — agents can read it"


def test_the_flows_own_note_is_not_mistaken_for_an_account():
    """`signin.py` writes "signed in from Connectors" — that is where the
    connection came from, not who it is."""
    out = shelf([{"host": "linkedin.com", "note": "signed in from Connectors"}])
    assert sub(out, "LinkedIn") == "linkedin.com · Signed in"


def test_a_site_added_by_hand_still_gets_a_card():
    """It would otherwise vanish from the shelf and appear only in the list
    below, which reads as the app having lost it."""
    out = shelf([{"host": "news.ycombinator.com", "note": ""}])
    assert sub(out, "news.ycombinator.com") \
        == "news.ycombinator.com · Allowed — agents can read it"
    assert "news.ycombinator.com" in out["on"]


def test_a_subdomain_matches_its_site():
    """A grant on www. or m. is the same site, and two cards for one account
    would be two Disconnect buttons that each half-work."""
    out = shelf([{"host": "www.linkedin.com", "note": "Suryansh Singh"}])
    assert sub(out, "LinkedIn") == "www.linkedin.com · Suryansh Singh"
    assert len(out["cards"]) == 5, "a duplicate card was made for the subdomain"


# ── the merge: one row, carrying everything both used to ──────────────────


def test_a_site_appears_exactly_once():
    """The bug. LinkedIn was drawn as a card *and* as `www.linkedin.com` in a
    list below, each with its own remove button calling the same endpoint —
    so the screen showed two of one thing and neither press meant more than
    the other."""
    out = shelf([{"host": "www.linkedin.com", "note": ""}])

    assert out["removers"] == ["www.linkedin.com"]
    assert len(out["cards"]) == 5, "one card per catalogue site, no extras"


def test_the_card_says_what_agents_may_do():
    """Moved up from the deleted row. A control that only offers the opposite
    makes you infer the present state from the label of the thing that would
    change it."""
    assert shelf([{"host": "x.com", "note": ""}])["caps"] == ["read only"]
    assert shelf([{"host": "x.com", "note": "", "may_act": True}])["caps"] \
        == ["read &amp; change"]


def test_the_permission_can_be_changed_from_the_same_row():
    """The toggle lived only on the deleted row. Losing it would have made the
    merge a feature removal wearing a tidy-up's clothes."""
    out = shelf([{"host": "x.com", "note": ""}])

    assert out["toggles"] == ["x.com"]


def test_a_site_nobody_has_added_offers_neither():
    """Permission controls for a site with no grant would be controls that
    cannot work."""
    out = shelf([])

    assert out["caps"] == [] and out["toggles"] == [] and out["removers"] == []


# ── a site you add by hand is dressed like the rest ───────────────────────


def test_a_hand_added_site_wears_its_own_logo():
    """It used to get the generic connectors glyph and a muted grey, so
    `figma.com` read as a row the app did not recognise — while Figma's mark
    sat in `BRAND_MARKS` the whole time, fetched for the connector list. The
    same marks serve both screens."""
    got = shelf([], probe=["figma.com", "www.stripe.com"])["byHand"]

    assert got["figma.com"]["tint"] == "#F24E1E"
    assert not got["figma.com"]["generic"] and not got["figma.com"]["monogram"]
    # `www.` is not part of the brand, and a lookup that kept it found nothing.
    assert got["www.stripe.com"]["tint"] == "#635BFF"


def test_a_site_with_no_published_mark_gets_a_letter_not_a_blank():
    """The same fallback the connector list uses. A letter is honest; twenty
    identical grey glyphs is a list you cannot scan."""
    got = shelf([], probe=["news.ycombinator.com"])["byHand"]

    assert got["news.ycombinator.com"]["monogram"]
    assert not got["news.ycombinator.com"]["generic"]
    assert got["news.ycombinator.com"]["tint"].startswith("hsl(")
