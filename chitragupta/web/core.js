/**
 * The primitives every part of the workspace uses.
 *
 * Loaded before `app.js` by `index.html`, in the same global scope — these are
 * plain scripts, not modules, because the test harnesses evaluate the app with
 * `new Function`, which compiles a script and cannot process `import`. See
 * docs/development/frontend-testing.md.
 *
 * **This file goes first, and that is not cosmetic.** `esc` and `md` have ~119
 * call sites; a `const` arrow read before its definition is a temporal-dead-zone
 * ReferenceError, which `node --check` passes and which has blanked a screen in
 * this app twice. Anything added here must not reach forward into `app.js`.
 *
 * What belongs here: things with no knowledge of the workspace's own structure.
 * Agent avatars, the sidebar, the icon wiring and every render path stay in
 * `app.js`, because they know what the page looks like.
 */

const $ = (s) => document.querySelector(s);
const api = (p, o) => fetch(p, o).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail || r.statusText)));

function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2200); }
// Has the user asked the system for less motion? A *function declaration*, not
// a const arrow: it is called from _bsDraw, which runs far above this point in
// the file, and a temporal-dead-zone ReferenceError here would blank the brain
// screen exactly the way one blanked the model drawer. Hoisting removes the
// question entirely.
function _lessMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
// Coerces first, deliberately. A connector tool answers with whatever shape it
// likes, so `esc()` gets handed objects and arrays — and `({}).replace` is not
// a function, so the escaper threw and the catch above it rendered the
// TypeError where the real message should have been. An escaper that can crash
// destroys the very thing it was asked to display.
function esc(s) {
  const t = s == null ? "" : (typeof s === "string" ? s : String(s));
  return t.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// gradient orb avatar per agent (stable colour from the id; the lead is always blue)
const ORB_COLORS = [["#8fb0ff", "#2f3a5e"], ["#7fd8b0", "#1f4636"], ["#c3a0f5", "#382a54"],
  ["#e0b489", "#48331f"], ["#e79aa0", "#48232e"], ["#9ad0e0", "#1e444f"], ["#b8c0cf", "#2b3140"]];
function orbPair(id) {
  let h = 0; for (const ch of String(id || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return ORB_COLORS[h % ORB_COLORS.length];
}
function orbStyle(id) { const [a, b] = orbPair(id); return `background:radial-gradient(circle at 32% 26%, ${a}, ${b} 74%)`; }

// ── minimal line icons (no emoji) ───────────────────────────────────────────
const _S = (p, s = 16) => `<svg viewBox="0 0 16 16" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
// Copy text, and say so.
//
// navigator.clipboard is the right API and http://127.0.0.1 counts as a secure
// context, so it is available — but it rejects without a user gesture and is
// absent in some webview configurations, and a copy button that silently does
// nothing is worse than no button. The execCommand path is the fallback: a
// throwaway textarea, off-screen rather than hidden, because a display:none
// element cannot hold a selection.
async function copyToClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fall through — the gesture may have been lost */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch (_) { return false; }
}

const IC = {
  brain: _S('<path d="M8 1.6l6.4 6.4L8 14.4 1.6 8z"/>'),
  connectors: _S('<rect x="2.5" y="3" width="11" height="2.6" rx="1"/><rect x="2.5" y="6.7" width="11" height="2.6" rx="1"/><rect x="2.5" y="10.4" width="8" height="2.4" rx="1"/>'),
  tasks: _S('<rect x="2.5" y="2.5" width="11" height="11" rx="2.5"/><path d="M5.4 8l1.7 1.7L11 5.9"/>'),
  // Three books on a shelf, the third leaning — a library of agents to pick
  // from. `applyIcons` keys off data-nav, so this name must stay "library".
  library: _S('<rect x="2.4" y="2.8" width="2.7" height="10.4" rx="0.8"/><rect x="6" y="4.4" width="2.7" height="8.8" rx="0.8"/><path d="M10.2 5.1l2.5.7-2 7.6-2.5-.7z"/>'),
  tools: _S('<path d="M2 5h6M11 5h3M2 11h3M8 11h6"/><circle cx="9.3" cy="5" r="1.5"/><circle cx="6" cy="11" r="1.5"/>'),
  model: _S('<circle cx="8" cy="8" r="5.6"/><path d="M8 2.4a5.6 5.6 0 0 1 0 11.2z" fill="currentColor" stroke="none"/>'),
  copy: _S('<rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 5.5v-1a1.5 1.5 0 0 0-1.5-1.5H4a1.5 1.5 0 0 0-1.5 1.5v5A1.5 1.5 0 0 0 4 11h1"/>', 14),
  tick: _S('<path d="M3.5 8.5l3 3 6-6.5"/>', 14),
  inbox: _S('<path d="M2.2 9h3.4l1 2h6.8l1-2h3.4"/><path d="M2.2 9 4.6 3.3h6.8L13.8 9v3.6a1.2 1.2 0 0 1-1.2 1.2H3.4a1.2 1.2 0 0 1-1.2-1.2z"/>'),
  help: _S('<circle cx="8" cy="8" r="6"/><path d="M6.2 6.2a1.9 1.9 0 0 1 3.6.7c0 1.3-1.8 1.5-1.8 2.7"/><circle cx="8" cy="11.4" r=".55" fill="currentColor" stroke="none"/>'),
  message: _S('<path d="M2.5 4.5h11v6.5H7l-3 2v-2H2.5z"/>'),
  search: _S('<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2L14 14"/>'),
  spark: _S('<path d="M8 1.6l1.5 4.9L14 8l-4.5 1.5L8 14.4 6.5 9.5 2 8l4.5-1.5z"/>'),
  plus: _S('<path d="M8 3.5v9M3.5 8h9"/>', 18),
  mic: _S('<rect x="6" y="2" width="4" height="7.5" rx="2"/><path d="M4 8a4 4 0 0 0 8 0M8 11.5V14"/>', 17),
  arrowUp: _S('<path d="M8 12.5V4M4.5 7.5L8 4l3.5 3.5"/>', 17),
  //: Filled, and rounded rather than a hard square — it sits inside a 34px
  //: circle, where a sharp-cornered square reads as a rendering artefact.
  stop: _S('<rect x="5" y="5" width="6" height="6" rx="1.4" fill="currentColor" stroke="none"/>', 17),
  lock: _S('<rect x="3.5" y="7" width="9" height="6" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>', 13),
  cloud: _S('<path d="M5 12a3 3 0 0 1 .3-6 3.5 3.5 0 0 1 6.6.8A2.6 2.6 0 0 1 11.5 12z"/>', 13),
  clock: _S('<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.4 1.4"/>'),
  bolt: _S('<path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z"/>'),
  //: The glyph set. These replaced the emoji and dingbats that were doing an
  //: icon's job — 🔒 in nine places, 📁, ✨, ⚠️, and ✕/✓/↗ as button faces.
  //: An emoji is a colour font the OS picks: it ignores `currentColor`, so it
  //: could not take the gold accent, sat at a different weight to every drawn
  //: icon beside it, and rendered differently per macOS version. A typographic
  //: arrow inside a sentence ("Add agent →") is deliberately NOT one of these —
  //: the design brief asks for exactly that.
  close: _S('<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>', 14),
  check: _S('<path d="M3.5 8.4l3 3 6-6.4"/>', 14),
  warn: _S('<path d="M8 2.6l6 10.8H2z"/><path d="M8 6.6v3.1"/><circle cx="8" cy="11.6" r=".6" fill="currentColor" stroke="none"/>', 14),
  external: _S('<path d="M6.5 3.5H3.4v9.1h9.1V9.5"/><path d="M9.2 3.5h3.3v3.3M12.5 3.5L7.4 8.6"/>', 13),
  folder: _S('<path d="M2 4.6h4l1.2 1.6h6.8v6.4a1.2 1.2 0 0 1-1.2 1.2H3.2A1.2 1.2 0 0 1 2 12.6z"/>', 13),
  chevronDown: _S('<path d="M4 6.2L8 10l4-3.8"/>', 12),
  arrowLeft: _S('<path d="M12.5 8h-9M7 3.5L2.5 8 7 12.5"/>', 14),
  settings: _S('<circle cx="8" cy="8" r="2.3"/><path d="M8 1.9v1.7M8 12.4v1.7M2.6 8H4.3M11.7 8h1.7M4.2 4.2l1.2 1.2M10.6 10.6l1.2 1.2M11.8 4.2l-1.2 1.2M5.4 10.6l-1.2 1.2"/>'),
  attach: _S('<path d="M12 6.5l-5 5a2.4 2.4 0 0 1-3.4-3.4l5.2-5.2a1.6 1.6 0 0 1 2.3 2.3l-5.2 5.2a.8.8 0 0 1-1.1-1.1L9.5 5"/>', 17),
  // Settings grew an Account page and the rail draws itself from this table, so
  // the key has to exist or that row renders with an empty icon slot.
  account: _S('<circle cx="8" cy="5.6" r="2.7"/><path d="M2.9 14c0-2.9 2.3-4.7 5.1-4.7s5.1 1.8 5.1 4.7"/>'),
};

// tiny, safe markdown renderer (escapes first, then applies a subset)
function mdInline(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])((https?:\/\/[^\s<)]+))/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}
function md(src) {
  const lines = esc(src).split("\n");
  let html = "", inList = false, inCode = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    if (/^```/.test(raw)) {
      if (inCode) { html += "</code></pre>"; inCode = false; }
      else { closeList(); html += "<pre><code>"; inCode = true; }
      continue;
    }
    if (inCode) { html += raw + "\n"; continue; }
    const h = raw.match(/^(#{1,4})\s+(.*)/);
    if (h) { closeList(); const lvl = Math.min(h[1].length + 2, 6); html += `<h${lvl}>${mdInline(h[2])}</h${lvl}>`; continue; }
    const li = raw.match(/^\s*[-*]\s+(.*)/) || raw.match(/^\s*\d+\.\s+(.*)/);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${mdInline(li[1])}</li>`; continue; }
    if (raw.trim() === "") { closeList(); continue; }
    closeList(); html += `<p>${mdInline(raw)}</p>`;
  }
  closeList(); if (inCode) html += "</code></pre>";
  return html;
}
