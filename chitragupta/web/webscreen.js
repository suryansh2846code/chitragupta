/**
 * The browser, inside the app.
 *
 * Chromium's own window is minimised from the moment it starts — `docs/BROWSER.md`
 * chose a real browser over an embedded WKWebView because a WKWebView cannot be
 * automated, and that is untouched. What changed is where the picture of it is
 * shown: here, rather than a second application appearing over the user's work.
 *
 * **"Visible, not headless" is kept.** That invariant is about a person being
 * able to watch and to stop, and about MFA needing a window somebody can reach.
 * The page is on a screen in the app they already have open, they can click and
 * type into it, and "Open window" brings the real one back for the rare thing
 * that needs it — a file picker, a system prompt.
 *
 * The picture is a JPEG polled a few times a second, not a video stream. A
 * stream would mean a socket, a codec and a reconnection story for a panel most
 * people open occasionally; frames are a `<img src=data:>` and stop costing
 * anything the moment the screen is closed.
 *
 * Clicks map straight through because the viewport is fixed server-side
 * (`driver.VIEWPORT`), so image pixels and page pixels are the same coordinate
 * space up to one scale factor.
 */

//: How often a new frame is asked for while the screen is open. Four a second
//: looks live without turning a background panel into a video call; every frame
//: is a full screenshot of a real page.
const WS_FRAME_MS = 250;

//: The page is rendered at this size server-side. Kept here only as the
//: fallback for the first frame — after that the server says so with each one,
//: and the server is the one that decides.
let WS_SIZE = { width: 1280, height: 800 };

let WS_TIMER = null;
let WS_BUSY = false;
let WS_WINDOW_OPEN = false;

/** Draw both window controls from one fact, so they cannot disagree. */
function wsSetHidden(hidden) {
  const hide = $("#wsHidden");
  if (hide) {
    hide.dataset.on = hidden ? "1" : "";
    hide.textContent = hidden ? "Show in Dock" : "Hide from Dock";
  }
  // Never show a control that cannot work: with no window there is nothing to
  // open, and a button that answered "there is no window" would be the app
  // offering something it knows is impossible.
  const win = $("#wsWindow");
  if (win) win.hidden = Boolean(hidden);
}

function wsNote(message) {
  const el = $("#wsNote");
  if (!el) return;
  el.hidden = !message;
  el.textContent = message || "";
}

/** One frame, and the address that goes with it. */
async function wsTick() {
  // Never two in flight. A slow frame would otherwise queue behind itself until
  // the browser thread is the only thing this app is doing.
  if (WS_BUSY) return;
  WS_BUSY = true;
  try {
    const r = await api("/api/browser/view");
    const img = $("#wsFrame");
    if (img) img.src = `data:image/jpeg;base64,${r.image}`;
    if (r.width && r.height) WS_SIZE = { width: r.width, height: r.height };
    const url = $("#wsUrl");
    // Never while they are typing: replacing the address under somebody's
    // cursor is how a half-typed one disappears.
    if (url && document.activeElement !== url) url.value = r.url || "";
    const title = $("#wsTitle");
    if (title) title.textContent = r.title || "";
    wsNote("");
  } catch (e) {
    wsNote(String(e).replace(/^Error:\s*/, ""));
  }
  WS_BUSY = false;
}

/** Where on the PAGE a click on the picture landed. */
function wsPoint(ev) {
  const img = $("#wsFrame");
  const box = img.getBoundingClientRect();
  if (!box.width || !box.height) return null;
  return {
    x: ((ev.clientX - box.left) / box.width) * WS_SIZE.width,
    y: ((ev.clientY - box.top) / box.height) * WS_SIZE.height,
  };
}

async function wsSend(body) {
  try {
    await api("/api/browser/view/input", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
  } catch (e) {
    wsNote(String(e).replace(/^Error:\s*/, ""));
    return;
  }
  // Straight after, rather than waiting for the next tick: a click that takes a
  // quarter-second to show reads as a click that did not register.
  wsTick();
}

function openWebScreen() {
  const screen = $("#webScreen");
  if (!screen) return;
  screen.hidden = false;
  wsNote("Starting the browser…");
  // Which way it is running is the server's fact, read once on open rather than
  // remembered here — this screen can be opened in a window that was reloaded
  // since the switch was last pressed.
  api("/api/browser/status")
    .then((s) => wsSetHidden(Boolean(s && s.hidden)))
    .catch(() => {});
  wsTick();
  if (!WS_TIMER) WS_TIMER = setInterval(wsTick, WS_FRAME_MS);
}

function closeWebScreen() {
  const screen = $("#webScreen");
  if (screen) screen.hidden = true;
  // Stopped, not left running. A panel nobody is looking at that goes on
  // screenshotting a page four times a second is the "spinner with no end
  // state" failure wearing a different hat.
  if (WS_TIMER) { clearInterval(WS_TIMER); WS_TIMER = null; }
}

{
  const frame = $("#wsFrame");
  if (frame) {
    frame.onclick = (e) => {
      const at = wsPoint(e);
      if (at) wsSend({ kind: "click", x: at.x, y: at.y });
    };
    frame.onwheel = (e) => {
      e.preventDefault();
      wsSend({ kind: "wheel", x: e.deltaX, y: e.deltaY });
    };
    // The picture takes focus so keystrokes have somewhere to go.
    frame.tabIndex = 0;
    frame.onkeydown = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;   // leave app shortcuts alone
      e.preventDefault();
      if (e.key.length === 1) return wsSend({ kind: "text", text: e.key });
      const named = {
        Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Escape: "Escape",
        ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
        ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight",
        Delete: "Delete", Home: "Home", End: "End",
      }[e.key];
      if (named) wsSend({ kind: "key", text: named });
    };
  }

  const url = $("#wsUrl");
  if (url) url.onkeydown = async (e) => {
    if (e.key !== "Enter") return;
    const wanted = url.value.trim();
    if (!wanted) return;
    wsNote("Opening…");
    try {
      await api("/api/browser/view/goto", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: wanted }) });
      wsNote("");
    } catch (err) {
      // The server's own sentence — "only https addresses can be used" tells
      // somebody what to change; a 400 does not.
      wsNote(String(err).replace(/^Error:\s*/, ""));
    }
    wsTick();
  };

  const back = $("#wsBack");
  if (back) back.onclick = () => wsSend({ kind: "key", text: "Alt+ArrowLeft" });

  const win = $("#wsWindow");
  if (win) win.onclick = async () => {
    WS_WINDOW_OPEN = !WS_WINDOW_OPEN;
    win.textContent = WS_WINDOW_OPEN ? "Hide window" : "Open window";
    try {
      await api("/api/browser/view/window", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visible: WS_WINDOW_OPEN }) });
    } catch (e) { wsNote(String(e).replace(/^Error:\s*/, "")); }
  };

  // Running with no window at all. Measured rather than promised: hidden means
  // headless, and the only thing a page can tell is that the user-agent says
  // HeadlessChrome instead of Chrome — everything else is identical. What it
  // really costs is the window, so "Open window" cannot work while it is on and
  // is hidden rather than left there to fail.
  const hide = $("#wsHidden");
  if (hide) hide.onclick = async () => {
    const turningOn = hide.dataset.on !== "1";
    if (turningOn && !confirm(
        "Run the browser with no window and no Dock icon?\n\n"
        + "It keeps working exactly as it does now and you still watch it here. "
        + "Two things change: websites can tell it is running this way, and "
        + "there is no window left to open for anything that needs a real one "
        + "— a file upload, a system prompt.\n\n"
        + "The browser restarts. You can turn this back off here.")) return;
    hide.disabled = true;
    try {
      const r = await api("/api/browser/hidden", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden: turningOn }) });
      wsSetHidden(Boolean(r.hidden));
      wsNote(r.hidden ? "Running with no window. Restarting the browser…"
                      : "The browser has a window again. Restarting…");
    } catch (e) { wsNote(String(e).replace(/^Error:\s*/, "")); }
    hide.disabled = false;
    wsTick();
  };

  const close = $("#wsClose");
  if (close) close.onclick = () => closeWebScreen();
}

window.addEventListener("keydown", (e) => {
  const screen = $("#webScreen");
  if (e.key === "Escape" && screen && !screen.hidden) closeWebScreen();
});
