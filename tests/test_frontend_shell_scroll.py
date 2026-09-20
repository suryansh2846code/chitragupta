"""The window never scrolls — the conversation does.

A long conversation used to scroll the whole DOCUMENT: the sidebar slid up
together with the chat and the composer left the bottom of the window, so the
user could not type without scrolling back down.

The cause is a CSS Grid rule that is easy to reintroduce and impossible to see
in the source. `.app` is `height: 100vh`, but its single row was `auto`, and a
grid item whose `overflow` is `visible` takes its CONTENT height as its
automatic minimum size. `.chat` therefore refused to shrink: measured at
**2234px inside a 913px row**. The row grew, the document overflowed, and
`.messages` — which is supposed to be the only scroller — never scrolled at
all (scrollHeight == clientHeight).

Neither a token test nor a source-order test catches that: every individual
declaration reads as correct. It only shows up under real layout, so this
test measures the real thing in a headless browser and skips where there
isn't one.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

WEB = Path(__file__).parent.parent / "chitragupta/web"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

VIEWPORT_W, VIEWPORT_H = 1400, 900

#: Enough conversation to overflow any plausible window.
TURNS = 40

PROBE = """
<script>
window.addEventListener('load', () => {
  const d = document.documentElement;
  const m = document.querySelector('.messages');
  const c = document.querySelector('.composer');
  console.log('LAYOUT ' + JSON.stringify({
    viewport: window.innerHeight,
    document_scroll_height: d.scrollHeight,
    app_height: Math.round(document.querySelector('.app').getBoundingClientRect().height),
    chat_height: Math.round(document.querySelector('.chat').getBoundingClientRect().height),
    messages_scroll_height: m.scrollHeight,
    messages_client_height: m.clientHeight,
    composer_bottom: Math.round(c.getBoundingClientRect().bottom),
  }));
});
</script>
"""


def _find_chrome() -> str | None:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return shutil.which("chromium") or shutil.which("google-chrome")


def _measure() -> dict:
    """Render the real index.html + styles.css and report its geometry."""
    chrome = _find_chrome()
    if not chrome:
        pytest.skip("no headless-capable browser on this machine")

    html = (WEB / "index.html").read_text()
    # The app's scripts would fetch a server that is not running, and there are
    # ten of them now rather than one — strip every local <script src>, since
    # the shell under test is pure CSS.
    html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>\s*</script>', "", html)
    # Absolute stylesheet href so the page can live in a temp dir.
    html = html.replace('href="/static/styles.css"', f'href="{(WEB / "styles.css").as_uri()}"')
    html = html.replace('href="styles.css"', f'href="{(WEB / "styles.css").as_uri()}"')

    turns = "".join(
        '<div class="msg user">summarize my mail</div>'
        '<div class="msg assistant"><div class="a-body">'
        "<p>A reply long enough that forty of them cannot fit in one window, which "
        "is the only condition under which this bug appears at all.</p></div></div>"
        for _ in range(TURNS)
    )
    # Matched by id rather than by the whole opening tag. The tag grows
    # attributes for reasons that have nothing to do with scrolling — `aria-busy`
    # arrived with the loading skeletons and broke this on an exact string,
    # which said "the messages container moved" when it had not moved at all.
    opening = re.search(r'<div id="messages"[^>]*>', html)
    assert opening, "the messages container moved — update this test"
    html = html.replace(opening.group(0), opening.group(0) + turns, 1)
    html = html.replace("</body>", PROBE + "</body>")

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "shell.html"
        page.write_text(html)
        proc = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             "--allow-file-access-from-files", "--virtual-time-budget=3000",
             f"--window-size={VIEWPORT_W},{VIEWPORT_H}",
             "--enable-logging=stderr", "--v=0", page.as_uri()],
            capture_output=True, text=True, timeout=90,
        )

    for line in proc.stderr.splitlines():
        if "LAYOUT " in line:
            blob = line.split("LAYOUT ", 1)[1]
            return json.loads(blob[: blob.rindex("}") + 1])
    pytest.fail(f"the probe never reported geometry:\n{proc.stderr[-2000:]}")


@pytest.fixture(scope="module")
def layout() -> dict:
    return _measure()


def test_the_window_itself_does_not_scroll(layout):
    """The sidebar must not slide away when the conversation gets long."""
    assert layout["document_scroll_height"] <= layout["viewport"] + 1, (
        "the whole document scrolls, so the sidebar scrolls with the chat — "
        f"document is {layout['document_scroll_height']}px in a "
        f"{layout['viewport']}px window. `.app` needs a constrained row "
        "(grid-template-rows: minmax(0, 1fr)) and `.chat` needs min-height: 0."
    )


def test_the_chat_column_stays_inside_its_row(layout):
    assert layout["chat_height"] <= layout["app_height"] + 1, (
        f"`.chat` is {layout['chat_height']}px inside a {layout['app_height']}px "
        "shell — a grid item with overflow:visible takes its content height as "
        "its automatic minimum size unless min-height: 0 says otherwise."
    )


def test_the_conversation_is_the_thing_that_scrolls(layout):
    assert layout["messages_scroll_height"] > layout["messages_client_height"], (
        "`.messages` is not scrolling internally — its content and its box are "
        "the same height, which means the overflow went somewhere else."
    )


def test_the_composer_stays_on_screen(layout):
    assert layout["composer_bottom"] <= layout["viewport"], (
        f"the composer ends at {layout['composer_bottom']}px in a "
        f"{layout['viewport']}px window — the user has to scroll to type."
    )
