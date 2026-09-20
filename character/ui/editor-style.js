/**
 * The editor's stylesheet, as a string.
 *
 * One source, two artifacts: the editor injects this once at mount, and the
 * build writes the identical bytes to `ui/editor.css` for hosts that would
 * rather ship a `<link>`. A second hand-maintained copy would drift, and the
 * copy that drifts is always the one the user's browser actually loaded.
 *
 * Everything is namespaced under `.ce-` and every colour is a custom property
 * on `.ce`. A host restyles the whole editor by setting six variables — no
 * overriding of individual rules, no specificity war, and no need for this file
 * to know anything about the product it has been dropped into.
 *
 * The defaults are dark because the editor is a tool panel that sits beside a
 * bright preview; a light panel next to a coloured character makes judging the
 * character's own contrast impossible.
 *
 * **No backticks below this line, not even inside a CSS comment.** The whole
 * stylesheet is one template literal, so a backtick closes it and everything
 * after becomes JavaScript. It fails at parse time, which means the module —
 * and, in the bundle, the entire package including the renderer — never loads.
 * `test/editor.test.js` imports this file for exactly that reason.
 */

export const EDITOR_CSS = `
.ce {
  --ce-bg: #0d0d0f;
  --ce-panel: #17171a;
  --ce-raised: #1f1f23;
  --ce-line: #2c2c31;
  --ce-text: #ecedf0;
  --ce-dim: #8d8f97;
  --ce-accent: #e2542a;
  --ce-accent-soft: #3a1a10;
  --ce-radius: 12px;
  --ce-gap: 10px;

  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 20px;
  align-items: start;
  color: var(--ce-text);
  font: 13px/1.45 var(--ce-font, ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif);
  -webkit-font-smoothing: antialiased;
}
.ce *, .ce *::before, .ce *::after { box-sizing: border-box; }
@media (max-width: 860px) { .ce { grid-template-columns: minmax(0, 1fr); } }

/* ── preview ─────────────────────────────────────────────────────────────── */
.ce-stage { display: flex; flex-direction: column; gap: 14px; align-items: center; }
.ce-frame {
  position: relative; width: 100%; max-width: 420px; aspect-ratio: 1;
  border-radius: 18px; overflow: hidden; background: var(--ce-panel);
  box-shadow: 0 18px 40px rgba(0,0,0,.35);
}
.ce-frame svg { display: block; width: 100%; height: 100%; }
/* The one control that sits ON the artwork, so it cannot take its colours from
   the theme: a character can be any colour, including the accent. It is opaque
   and always the same dark chip, and it opts out of the pressed-button styling
   every other button here uses — gold-on-gold over a pale character was
   unreadable, and its own switch already says which state it is in. */
.ce-follow, .ce button.ce-follow[aria-pressed="true"] {
  position: absolute; top: 12px; right: 12px; z-index: 2;
  display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 10px 6px 12px; border-radius: 999px;
  background: #16161a; font-size: 12px; color: #f2f2f4;
  cursor: pointer; user-select: none;
  border: 1px solid rgba(255,255,255,.14);
  box-shadow: 0 2px 10px rgba(0,0,0,.35);
}
.ce-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; width: 100%; max-width: 420px; }
.ce-export { display: flex; border-radius: 10px; overflow: hidden; border: 1px solid var(--ce-line); }
.ce-export button { border: 0; border-left: 1px solid var(--ce-line); border-radius: 0; }
.ce-export button:first-child { border-left: 0; background: var(--ce-raised); font-weight: 600; }

/* ── panel ───────────────────────────────────────────────────────────────── */
.ce-panel {
  background: var(--ce-panel); border: 1px solid var(--ce-line);
  border-radius: 16px; padding: 12px; display: flex; flex-direction: column;
  gap: 14px; max-height: min(78vh, 900px); overflow: hidden;
}
.ce-tabs { display: flex; gap: 6px; background: var(--ce-bg); padding: 5px; border-radius: 12px; }
.ce-tab {
  flex: 1; display: grid; place-items: center; height: 34px; border-radius: 8px;
  border: 1px solid transparent; background: transparent; color: var(--ce-dim); cursor: pointer;
}
.ce-tab:hover { color: var(--ce-text); }
.ce-tab[aria-selected="true"] { background: var(--ce-accent-soft); border-color: var(--ce-accent); color: var(--ce-accent); }
.ce-tab svg { width: 17px; height: 17px; }

.ce-scroll { overflow-y: auto; overflow-x: hidden; padding: 2px 6px 8px 2px; display: flex; flex-direction: column; gap: 20px; }
/* A tab's contents stack with real air between them. Without this the pane was
   one undivided run of controls: a grid of faces butted straight into the
   Eyes/Nose/Mouth tabs, which butted into the shape row, and nothing read as a
   group. Everything inside a group keeps the tighter .ce-section gap, so the
   difference between "these belong together" and "these do not" is visible. */
.ce-pane { display: flex; flex-direction: column; gap: 22px; }
.ce-scroll::-webkit-scrollbar { width: 8px; }
.ce-scroll::-webkit-scrollbar-thumb { background: var(--ce-line); border-radius: 8px; }

.ce-section { display: flex; flex-direction: column; gap: 12px; }
.ce-legend {
  display: flex; align-items: center; gap: 7px; color: var(--ce-dim);
  font-size: 11px; font-weight: 600; letter-spacing: .09em; text-transform: uppercase;
}
.ce-legend svg { width: 13px; height: 13px; }

/* ── controls ────────────────────────────────────────────────────────────── */
.ce-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ce-label { color: var(--ce-text); }
.ce-value { color: var(--ce-accent); font-variant-numeric: tabular-nums; font-weight: 600; font-size: 12px; }

.ce-slider { display: flex; flex-direction: column; gap: 2px; }
.ce-slider input[type="range"] {
  -webkit-appearance: none; appearance: none; width: 100%; height: 4px;
  background: transparent; cursor: pointer; margin: 6px 0;
}
.ce-slider input[type="range"]::-webkit-slider-runnable-track {
  height: 4px; border-radius: 3px;
  background: linear-gradient(to right, var(--ce-accent) var(--ce-fill, 50%), var(--ce-line) var(--ce-fill, 50%));
}
.ce-slider input[type="range"]::-moz-range-track { height: 4px; border-radius: 3px; background: var(--ce-line); }
.ce-slider input[type="range"]::-moz-range-progress { height: 4px; border-radius: 3px; background: var(--ce-accent); }
.ce-slider input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%;
  background: var(--ce-accent); margin-top: -5px; border: 0;
}
.ce-slider input[type="range"]::-moz-range-thumb { width: 14px; height: 14px; border: 0; border-radius: 50%; background: var(--ce-accent); }
.ce-slider input[type="range"]:focus-visible { outline: 2px solid var(--ce-accent); outline-offset: 4px; }

.ce button, .ce .ce-btn {
  font: inherit; color: var(--ce-text); background: var(--ce-raised);
  border: 1px solid var(--ce-line); border-radius: 10px;
  padding: 8px 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 7px;
}
.ce button:hover { border-color: #444; }
.ce button:focus-visible { outline: 2px solid var(--ce-accent); outline-offset: 2px; }
.ce button[aria-pressed="true"], .ce button.is-on {
  border-color: var(--ce-accent); background: var(--ce-accent-soft); color: var(--ce-accent);
}
.ce button svg { width: 15px; height: 15px; }
/* ...but a chip and a tab are buttons whose contents are NOT icons. The icon
   rule above is one class and two type selectors, which outranks a plain
   .ce-chip svg -- so every character thumbnail was rendered at 15px inside a
   71px chip. The faces looked unreadably small and the cause looked like the
   geometry, which it was not. Both of these are deliberately more specific. */
.ce button.ce-chip svg { width: 100%; height: 100%; }
.ce button.ce-tab svg { width: 17px; height: 17px; }
.ce-ghost { background: transparent; border-color: transparent; color: var(--ce-dim); }

.ce-grid { display: grid; gap: 8px; }
.ce-grid-7 { grid-template-columns: repeat(7, minmax(0, 1fr)); }
.ce-grid-5 { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.ce-grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.ce-grid-8 { grid-template-columns: repeat(8, minmax(0, 1fr)); }
.ce-grid-6 { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.ce-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.ce-grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }

.ce-chip {
  aspect-ratio: 1; display: grid; place-items: center; padding: 2px;
  background: var(--ce-raised); border: 1px solid var(--ce-line); border-radius: 10px;
  cursor: pointer; overflow: hidden;
}
.ce-chip:hover { border-color: var(--ce-dim); }
.ce-chip svg { width: 100%; height: 100%; }
.ce-chip[aria-pressed="true"] { border-color: var(--ce-accent); background: var(--ce-accent-soft); }

.ce-seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 8px; }
.ce-seg button { justify-content: center; flex-direction: column; gap: 5px; padding: 9px 6px; font-size: 12px; }

.ce-swatches { display: grid; grid-template-columns: repeat(8, minmax(0,1fr)); gap: 8px; }
.ce-swatch { aspect-ratio: 1; border-radius: 8px; border: 2px solid transparent; cursor: pointer; padding: 0; }
.ce-swatch[aria-pressed="true"] { border-color: var(--ce-text); }
.ce-swatch span { display: block; width: 100%; height: 100%; border-radius: 6px; }

.ce-color { display: flex; align-items: center; gap: 8px; }
.ce-color input[type="color"] {
  width: 30px; height: 26px; padding: 0; border: 1px solid var(--ce-line);
  border-radius: 7px; background: none; cursor: pointer;
}
.ce-color input[type="text"] {
  flex: 1; min-width: 0; font: inherit; font-variant-numeric: tabular-nums;
  color: var(--ce-text); background: var(--ce-bg); border: 1px solid var(--ce-line);
  border-radius: 8px; padding: 6px 8px;
}

.ce-num {
  width: 96px; font: inherit; text-align: right; font-variant-numeric: tabular-nums;
  color: var(--ce-text); background: var(--ce-bg); border: 1px solid var(--ce-line);
  border-radius: 8px; padding: 6px 9px;
}
.ce-text { width: 100%; font: inherit; color: var(--ce-text); background: var(--ce-bg);
  border: 1px solid var(--ce-line); border-radius: 8px; padding: 7px 9px; }

.ce-switch {
  position: relative; width: 38px; height: 22px; flex: none; border-radius: 999px;
  background: var(--ce-line); border: 0; padding: 0; cursor: pointer; transition: background .16s;
}
.ce-switch::after {
  content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px;
  border-radius: 50%; background: #fff; transition: transform .16s;
}
.ce-switch[aria-checked="true"] { background: var(--ce-accent); }
.ce-switch[aria-checked="true"]::after { transform: translateX(16px); }

.ce-partlist { display: flex; flex-direction: column; gap: 6px; }
.ce-part {
  display: flex; align-items: center; gap: 8px; padding: 7px 9px;
  background: var(--ce-raised); border: 1px solid var(--ce-line); border-radius: 10px; cursor: pointer;
}
.ce-part[aria-selected="true"] { border-color: var(--ce-accent); }
.ce-part-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ce-part-dot { width: 14px; height: 14px; border-radius: 4px; flex: none; border: 1px solid rgba(255,255,255,.18); }
.ce-part-host { font-size: 10px; color: var(--ce-accent); letter-spacing: .06em; text-transform: uppercase; }
.ce-icon-btn { padding: 5px; border-radius: 7px; background: transparent; border-color: transparent; color: var(--ce-dim); }
.ce-icon-btn:hover { color: var(--ce-text); background: var(--ce-bg); }

.ce-note { color: var(--ce-dim); font-size: 12px; line-height: 1.5; }
.ce-empty { color: var(--ce-dim); font-size: 12px; padding: 10px; text-align: center;
  border: 1px dashed var(--ce-line); border-radius: 10px; }

.ce-toast {
  position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
  background: rgba(12,12,14,.92); color: #fff; padding: 7px 12px; border-radius: 999px;
  font-size: 12px; pointer-events: none; opacity: 0; transition: opacity .18s;
  border: 1px solid rgba(255,255,255,.1);
}
.ce-toast.is-on { opacity: 1; }

@media (prefers-reduced-motion: reduce) {
  .ce-switch, .ce-switch::after, .ce-toast { transition: none; }
}
`;
