"""The browser, inside the app — the screen, executed rather than read.

Chromium's own window is minimised from the moment it starts, so this screen is
the only place a person can watch or steer it. That makes two things
load-bearing, and neither is visible to a source-order assertion:

* **The address is shown with the picture.** A page rendered without the address
  it came from is the one thing a browser must never do — it is how somebody
  types a password into a page they believe is their bank.
* **A click on the picture lands where the user aimed.** The frame is
  letterboxed, so image pixels and page pixels differ by a scale factor. Getting
  it wrong does not fail loudly; it puts every click a few pixels out, which on a
  dense page is a different control.

So the real functions are sliced out of `webscreen.js` and run.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "chitragupta/web"

#: What the server says a frame is. The page is rendered at a fixed size
#: server-side, which is what makes the mapping one scale factor rather than two.
FRAME = {"image": "TESTBYTES", "url": "https://web.whatsapp.com/",
         "title": "WhatsApp", "width": 1280, "height": 800}

#: The picture on screen, letterboxed to half size and offset by the chrome.
RECT = {"left": 100, "top": 50, "width": 640, "height": 400}


def drive(frame=None, clicks=(), rect=None) -> dict:
    payload = {"frame": frame or FRAME, "clicks": list(clicks),
               "rect": rect or RECT}
    proc = subprocess.run(
        ["node", str(ROOT / "tests/js/web_screen.mjs"), str(WEB / "webscreen.js")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


@pytest.fixture(scope="module")
def shown():
    return drive()


def test_the_screen_renders_without_throwing(shown):
    """`node --check` passes on the temporal-dead-zone error that blanked a whole
    screen once. This executes it."""
    assert shown["frames"][0]["hidden"] is False


def test_the_page_arrives_as_a_picture(shown):
    assert shown["frames"][0]["src"].startswith("data:image/jpeg;base64,")


def test_the_address_is_shown_beside_the_picture(shown):
    """The one thing a browser must never omit."""
    assert shown["frames"][0]["url"] == "https://web.whatsapp.com/"
    assert shown["frames"][0]["title"] == "WhatsApp"


def test_a_click_is_sent_as_a_point_on_the_page_not_on_the_image():
    """The picture is half size here, so a click 320 across and 200 down the
    image is 640 across and 400 down the page. Sending the image's own
    coordinates would land at a quarter of the intended distance."""
    out = drive(clicks=[{"clientX": 100 + 320, "clientY": 50 + 200}])

    (sent,) = out["sent"]
    (body,) = [b for b in sent if b and b.get("kind") == "click"]
    assert body["x"] == pytest.approx(640)
    assert body["y"] == pytest.approx(400)


def test_a_click_in_the_corner_maps_to_the_corner():
    out = drive(clicks=[{"clientX": 100, "clientY": 50}])

    (body,) = [b for b in out["sent"][0] if b and b.get("kind") == "click"]
    assert body["x"] == pytest.approx(0)
    assert body["y"] == pytest.approx(0)


def test_closing_the_screen_stops_it_watching(shown):
    """A panel nobody is looking at that goes on screenshotting a page several
    times a second is the "spinner with no end state" in a different hat."""
    assert shown["closedHidden"] is True
