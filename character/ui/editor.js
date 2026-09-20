/**
 * The editor.
 *
 * Framework-free on purpose. This package gets dropped into a React app, a
 * Vue app, a Django template and a vanilla page with a `<script>` tag, and a
 * component that picks one of those picks a fight with the other three.
 *
 * ── the one architectural decision worth reading ────────────────────────────
 *
 * **The DOM is built once. Updates change values, never structure.**
 *
 * Every control registers a `sync(doc)` closure when it is created, and a
 * document change walks that list. Nothing is re-rendered, nothing is replaced.
 *
 * The obvious alternative — rebuild the panel from the document on every change
 * — is what the first version did, and it is wrong in four ways that all show
 * up the moment someone actually uses it: the slider you are dragging is
 * destroyed mid-drag so the pointer capture dies and the value sticks; focus
 * leaves the field you are typing in after the first character; the scroll
 * position of a long panel jumps to the top on every nudge; and a colour
 * input's native picker closes itself. All four read as "this tool is broken",
 * and none of them are visible in a screenshot.
 *
 * The cost is that adding a control means writing its read *and* its write.
 * That is the trade, and it is the right one for a panel this dense.
 *
 * ── the other one ──────────────────────────────────────────────────────────
 *
 * **The document is the single source of truth, and it is replaced, not
 * mutated.** Every edit produces a new normalised document and hands it to the
 * host through `onChange`. The host may reject it, transform it, or feed it
 * back through an undo stack — the editor holds no state the host cannot see.
 */

import {
  normalize, cloneScene, defaultScene, defaultPart, LIMITS,
  EYE_SHAPES, MOUTH_SHAPES, NOSE_SHAPES, FRAMES, BACKGROUND_STYLES,
} from "../src/schema.js";
import { PALETTES, palette, rgbToHex, hexToRgb } from "../src/palettes.js";
import { SHAPES, SHAPE_LABELS } from "../src/primitives.js";
import { PRESETS, applyPreset } from "../src/presets.js";
import { randomScene } from "../src/generate.js";
import { createCharacter, renderToString } from "../src/renderer.js";
import { encode, decode } from "../src/codec.js";
import { downloadSVG, downloadPNG } from "../src/png.js";
import { EDITOR_CSS } from "./editor-style.js";

// ── DOM helpers ────────────────────────────────────────────────────────────

function h(tag, attrs, children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.setAttribute("style", v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const c of [].concat(children == null ? [] : children)) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return el;
}

/**
 * Icons are drawn, never typed.
 *
 * An emoji is a colour font the operating system chooses. It ignores
 * `currentColor`, so it can never take the accent when its control is selected;
 * it sits at its own weight beside every other glyph; and it changes shape
 * between OS versions, so the panel looks different on two machines running the
 * same build.
 */
const ICON = {
  face: "M2 5h12M2 8h8M2 11h5",
  parts: "M5.5 3.5a2 2 0 1 1-.01 0M10.5 9.5a2 2 0 1 1-.01 0M7 5.5h2.5a1.5 1.5 0 0 1 1.5 1.5v.5",
  colour: "M8 2a6 6 0 1 0 0 12c.7 0 1-.5 1-1s-.5-1-.5-1.5.4-.9 1-.9H11a3 3 0 0 0 3-3A5.6 5.6 0 0 0 8 2Z",
  effects: "M8 2v3M8 11v3M2 8h3M11 8h3M4.2 4.2l2 2M9.8 9.8l2 2M11.8 4.2l-2 2M6.2 9.8l-2 2",
  add: "M8 3v10M3 8h10",
  remove: "M4 4l8 8M12 4l-8 8",
  copy: "M5.5 5.5h7v7h-7zM3.5 10.5v-7h7",
  up: "M8 12V4M4.5 7.5 8 4l3.5 3.5",
  down: "M8 4v8M4.5 8.5 8 12l3.5-3.5",
  dice: "M3 5.5 8 3l5 2.5v5L8 13 3 10.5zM3 5.5 8 8l5-2.5M8 8v5",
  reset: "M3 8a5 5 0 1 1 1.6 3.7M3 5v3h3",
  link: "M6.5 9.5 9.5 6.5M6 4.5 7.5 3a2.5 2.5 0 0 1 3.5 3.5L9.5 8M6.5 8 5 9.5A2.5 2.5 0 0 0 8.5 13L10 11.5",
  star: "M8 2.5l1.6 3.4 3.4.4-2.5 2.4.7 3.6L8 10.6l-3.2 1.7.7-3.6L3 6.3l3.4-.4z",
};

function icon(path, title) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.35");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path);
  svg.appendChild(p);
  if (title) svg.setAttribute("aria-label", title);
  return svg;
}

let styleInjected = false;
function injectStyle(doc) {
  if (styleInjected) return;
  const d = doc || document;
  if (d.getElementById("character-editor-style")) { styleInjected = true; return; }
  const style = d.createElement("style");
  style.id = "character-editor-style";
  style.textContent = EDITOR_CSS;
  (d.head || d.documentElement).appendChild(style);
  styleInjected = true;
}

const titleCase = (s) => String(s).replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/**
 * Which controls appear under the preview unless a host says otherwise.
 *
 * Module scope, not inside `mountEditor`, and that is load-bearing:
 * `buildActions()` is called while the preview is being set up, which happens
 * *above* where this used to be declared. A `const` read before its definition
 * is a temporal-dead-zone `ReferenceError` — it is not hoisted the way a
 * function declaration is, and `node --check` passes on it.
 */
const DEFAULT_ACTIONS = ["randomize", "reset"];

// ── the editor ─────────────────────────────────────────────────────────────

/**
 * Mount the editor into `container`.
 *
 * options:
 *   document      the character to start from (default: a fresh one)
 *   onChange(doc) called after every edit, with a normalised document
 *   onSave(doc)   if given, a Save control appears; the host owns persistence
 *   storageKey    where saved presets live (default `character.presets`;
 *                 null disables the saved-presets row entirely)
 *   shareBase     base URL for "Copy link" (default: the current page)
 *   showPreview   set false to render only the panel, e.g. beside a preview
 *                 the host already draws somewhere else
 */
export function mountEditor(container, options) {
  const opts = options || {};
  const host = typeof container === "string" ? document.querySelector(container) : container;
  if (!host) throw new Error("mountEditor: container not found");
  injectStyle(host.ownerDocument);

  let doc = normalize(opts.document || defaultScene());
  let selectedPart = 0;
  let faceTab = "eyes";
  const syncs = [];
  const listeners = { change: [] };

  const root = h("div", { class: "ce" });
  host.appendChild(root);

  // ── preview ──────────────────────────────────────────────────────────────
  const frame = h("div", { class: "ce-frame" });
  const toast = h("div", { class: "ce-toast", role: "status" });
  const stage = h("div", { class: "ce-stage" });
  let instance = null;
  let followOn = doc.scene.follow.enabled;

  function say(message) {
    toast.textContent = message;
    toast.classList.add("is-on");
    clearTimeout(say._t);
    say._t = setTimeout(() => toast.classList.remove("is-on"), 1800);
  }

  if (opts.showPreview !== false) {
    const followBtn = h("button", {
      class: "ce-follow", type: "button", "aria-pressed": String(followOn),
      onclick: () => {
        followOn = !followOn;
        const next = cloneScene(doc);
        next.scene.follow.enabled = followOn;
        commit(next);
      },
    }, ["Follow cursor", h("span", { class: "ce-switch", "aria-checked": String(followOn), role: "presentation" })]);
    syncs.push((d) => {
      followOn = d.scene.follow.enabled;
      followBtn.setAttribute("aria-pressed", String(followOn));
      followBtn.querySelector(".ce-switch").setAttribute("aria-checked", String(followOn));
    });

    frame.appendChild(followBtn);
    frame.appendChild(toast);
    stage.appendChild(frame);
    stage.appendChild(buildActions());
    root.appendChild(stage);
  }

  const panel = h("div", { class: "ce-panel" });
  root.appendChild(panel);

  // ── tabs ─────────────────────────────────────────────────────────────────
  const TABS = [
    { id: "face", label: "Face", icon: ICON.face },
    { id: "body", label: "Body", icon: ICON.parts },
    { id: "colour", label: "Colour", icon: ICON.colour },
    { id: "effects", label: "Effects", icon: ICON.effects },
  ];
  let activeTab = "face";
  const tabBar = h("div", { class: "ce-tabs", role: "tablist" });
  const scroll = h("div", { class: "ce-scroll" });
  const panes = {};

  for (const t of TABS) {
    const btn = h("button", {
      class: "ce-tab", type: "button", role: "tab", title: t.label,
      "aria-selected": String(t.id === activeTab), "aria-label": t.label,
      onclick: () => selectTab(t.id),
    }, [icon(t.icon)]);
    t.btn = btn;
    tabBar.appendChild(btn);
    panes[t.id] = h("div", { class: "ce-pane", role: "tabpanel", "aria-label": t.label });
  }

  function selectTab(id) {
    activeTab = id;
    for (const t of TABS) t.btn.setAttribute("aria-selected", String(t.id === id));
    scroll.textContent = "";
    scroll.appendChild(panes[id]);
    scroll.scrollTop = 0;
  }

  panel.appendChild(tabBar);
  panel.appendChild(scroll);

  buildFaceTab(panes.face);
  buildBodyTab(panes.body);
  buildColourTab(panes.colour);
  buildEffectsTab(panes.effects);
  selectTab("face");

  // ── the edit cycle ───────────────────────────────────────────────────────

  /**
   * Accept an edit.
   *
   * Every control funnels through here, and this is the only place the document
   * is replaced. `skipSync` exists for the one case where syncing back would be
   * wrong: the control that originated the edit is mid-interaction, and writing
   * a rounded value back into a slider the user is still dragging makes it stutter.
   */
  function commit(next, skipSync) {
    doc = normalize(next);
    if (instance) instance.setDocument(doc);
    if (!skipSync) for (const fn of syncs) fn(doc);
    if (opts.onChange) opts.onChange(cloneScene(doc));
    for (const fn of listeners.change) fn(cloneScene(doc));
  }

  /** Read-modify-write on a copy. The mutating idiom, without mutating state. */
  function edit(fn, skipSync) {
    const next = cloneScene(doc);
    fn(next.scene, next);
    commit(next, skipSync);
  }

  if (opts.showPreview !== false) {
    instance = createCharacter(frame, doc, {
      draggable: true,
      // `replace: false` is load-bearing. The default empties the container,
      // and the container already holds the follow-cursor toggle and the toast
      // — so the default silently deleted both, leaving a preview with no
      // controls on it and no error anywhere to say why.
      replace: false,
      onChange: (d) => commit(d),
      title: doc.metadata.name,
    });
    // Behind the overlay controls, which were appended first.
    frame.insertBefore(instance.el, frame.firstChild);
  }

  // ── actions ──────────────────────────────────────────────────────────────

  /**
   * The controls under the preview.
   *
   * Export and share are **opt-in**, via `actions`. A character is usually a
   * profile picture inside a product, and there "Download SVG / PNG 256 /
   * PNG 512 / Copy link" is four controls answering a question nobody asked —
   * they crowd out the two that matter, Surprise me and Reset. A host that
   * genuinely hands out files asks for them:
   *
   *     mountEditor(el, { actions: ["randomize", "reset", "export", "share"] })
   *
   * `downloadSVG`, `downloadPNG` and `encode` stay on the package's public API
   * either way, so a host can put export wherever it belongs in its own UI.
   */
  function buildActions() {
    const wanted = Array.isArray(opts.actions) ? opts.actions : DEFAULT_ACTIONS;
    const on = (name) => wanted.indexOf(name) >= 0;
    const png = (px) => {
      downloadPNG(doc, px).then(() => say(`PNG ${px} downloaded`))
        .catch(() => say("Could not make a PNG here"));
    };

    return h("div", { class: "ce-actions" }, [
      on("export") && h("div", { class: "ce-export" }, [
        h("button", { type: "button", onclick: () => { downloadSVG(doc); say("SVG downloaded"); } }, "Download SVG"),
        h("button", { type: "button", onclick: () => png(256) }, "PNG 256"),
        h("button", { type: "button", onclick: () => png(512) }, "PNG 512"),
      ]),
      on("randomize") && h("button", { type: "button", onclick: () => commit(randomScene({ name: doc.metadata.name })) },
        [icon(ICON.dice), "Surprise me"]),
      on("reset") && h("button", { class: "ce-ghost", type: "button", onclick: () => commit(defaultScene()) },
        [icon(ICON.reset), "Reset"]),
      on("share") && h("button", { type: "button", onclick: copyLink }, [icon(ICON.link), "Copy link"]),
      opts.onSave && h("button", { type: "button", onclick: () => opts.onSave(cloneScene(doc)) },
        [icon(ICON.star), "Save"]),
    ]);
  }

  function copyLink() {
    const base = opts.shareBase || (typeof location !== "undefined" ? location.href.split("#")[0].split("?")[0] : "");
    const url = `${base}?c=${encode(doc)}`;
    // The payload goes in the query string so it survives being pasted into a
    // chat client that strips fragments. It is never sent anywhere by us.
    const write = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(url)
      : Promise.reject(new Error("no clipboard"));
    write.then(() => say("Link copied")).catch(() => {
      // Clipboard access is refused without a user gesture in some webviews,
      // and a button that silently does nothing is worse than no button.
      const ta = h("textarea", { style: "position:fixed;left:-9999px;top:0" });
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); say("Link copied"); } catch { say("Could not copy the link"); }
      document.body.removeChild(ta);
    });
  }

  // ── control factories ────────────────────────────────────────────────────

  /** A labelled slider bound to one path in the document. */
  function slider(label, limits, read, write) {
    const value = h("span", { class: "ce-value" });
    const input = h("input", {
      type: "range", min: limits.min, max: limits.max, step: limits.step,
      "aria-label": label,
    });
    const paint = (v) => {
      const pct = ((v - limits.min) / (limits.max - limits.min)) * 100;
      input.style.setProperty("--ce-fill", `${pct}%`);
      value.textContent = format(v, limits);
    };
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      paint(v);
      // `true`: do not write back into this slider while it is being dragged.
      edit((s, d) => write(s, v, d), true);
    });
    input.addEventListener("change", () => { for (const fn of syncs) fn(doc); });
    syncs.push((d) => { const v = read(d.scene, d); input.value = String(v); paint(v); });
    return h("div", { class: "ce-slider" }, [
      h("div", { class: "ce-row" }, [h("span", { class: "ce-label" }, label), value]),
      input,
    ]);
  }

  function format(v, limits) {
    const dp = limits.step >= 1 ? 0 : limits.step >= 0.1 ? 1 : 2;
    return `${Number(v).toFixed(dp)}${limits.unit || ""}`;
  }

  /** A number field, for values people type rather than sweep. */
  function number(label, limits, read, write) {
    const input = h("input", {
      class: "ce-num", type: "number", min: limits.min, max: limits.max, step: limits.step,
      "aria-label": label,
    });
    input.addEventListener("change", () => {
      const v = parseFloat(input.value);
      if (Number.isFinite(v)) edit((s, d) => write(s, v, d));
      else for (const fn of syncs) fn(doc);
    });
    syncs.push((d) => { if (document.activeElement !== input) input.value = String(read(d.scene, d)); });
    return h("div", { class: "ce-row" }, [
      h("span", { class: "ce-label" }, label),
      h("div", { class: "ce-color" }, [input, limits.unit ? h("span", { class: "ce-note" }, limits.unit) : null]),
    ]);
  }

  /** An on/off row. */
  function toggle(label, read, write) {
    const sw = h("button", { class: "ce-switch", type: "button", role: "switch", "aria-label": label });
    sw.addEventListener("click", () => edit((s, d) => write(s, !read(s, d), d)));
    syncs.push((d) => sw.setAttribute("aria-checked", String(!!read(d.scene, d))));
    return h("div", { class: "ce-row" }, [h("span", { class: "ce-label" }, label), sw]);
  }

  /** A row of mutually exclusive buttons. */
  function segmented(items, read, write, cls) {
    const wrap = h("div", { class: cls || "ce-seg" });
    const btns = items.map((it) => {
      const b = h("button", { type: "button", onclick: () => edit((s, d) => write(s, it.value, d)) },
        [it.label]);
      wrap.appendChild(b);
      return b;
    });
    syncs.push((d) => {
      const cur = read(d.scene, d);
      items.forEach((it, i) => btns[i].setAttribute("aria-pressed", String(it.value === cur)));
    });
    return wrap;
  }

  /**
   * A colour control that can also mean "inherit".
   *
   * Every colour in the document is nullable, and null means "follow the
   * palette". Without an explicit way back to null, choosing a palette after
   * touching one colour leaves that colour stranded on the old value — and the
   * person has no way to tell the editor they changed their mind short of
   * starting over.
   */
  function colour(label, read, write, fallback) {
    const swatch = h("input", { type: "color", "aria-label": label });
    const text = h("input", { type: "text", spellcheck: "false", "aria-label": `${label} hex` });
    const clear = h("button", { class: "ce-icon-btn", type: "button", title: "Use the palette colour" }, [icon(ICON.reset)]);

    swatch.addEventListener("input", () => edit((s, d) => write(s, swatch.value, d), true));
    swatch.addEventListener("change", () => { for (const fn of syncs) fn(doc); });
    text.addEventListener("change", () => {
      const v = text.value.trim();
      if (/^#?[0-9a-fA-F]{6}$/.test(v)) edit((s, d) => write(s, v[0] === "#" ? v : `#${v}`, d));
      else for (const fn of syncs) fn(doc);
    });
    clear.addEventListener("click", () => edit((s, d) => write(s, null, d)));

    syncs.push((d) => {
      const raw = read(d.scene, d);
      const shown = raw || fallback(d);
      swatch.value = shown;
      if (document.activeElement !== text) text.value = raw ? raw : `${shown}  (palette)`;
      clear.style.visibility = raw ? "visible" : "hidden";
    });

    return h("div", { class: "ce-section" }, [
      h("span", { class: "ce-label" }, label),
      h("div", { class: "ce-color" }, [swatch, text, clear]),
    ]);
  }

  function legend(text, path) {
    return h("div", { class: "ce-legend" }, [icon(path), text]);
  }

  /** A grid of character thumbnails, re-rendered only when its signature changes. */
  function thumbGrid(cls, items, build, isActive, onPick) {
    const grid = h("div", { class: `ce-grid ${cls}` });
    let signature = null;
    const chips = items.map((it) => {
      const b = h("button", { class: "ce-chip", type: "button", title: it.name, "aria-label": it.name,
        onclick: () => onPick(it) });
      grid.appendChild(b);
      return b;
    });
    syncs.push((d) => {
      const sig = build.signature(d);
      if (sig !== signature) {
        signature = sig;
        items.forEach((it, i) => { chips[i].innerHTML = renderToString(build.doc(d, it), { size: 44, idPrefix: `t${cls}${i}` }); });
      }
      items.forEach((it, i) => chips[i].setAttribute("aria-pressed", String(isActive(d, it))));
    });
    return grid;
  }

  // ── face tab ─────────────────────────────────────────────────────────────

  function buildFaceTab(pane) {
    if (opts.storageKey !== null) pane.appendChild(buildSavedPresets());

    pane.appendChild(h("div", { class: "ce-section" }, [
      legend("Face", ICON.face),
      thumbGrid("ce-grid-4", FACE_PRESETS, {
        signature: (d) => d.scene.appearance.paletteId,
        doc: (d, it) => faceThumb(d, it.face),
      }, (d, it) => faceMatches(d.scene.face, it.face), (it) => edit((s) => {
        s.face = Object.assign({}, s.face, it.face, { enabled: true });
      })),
    ]));

    const subBar = h("div", { class: "ce-seg" });
    const subs = [["eyes", "Eyes"], ["nose", "Nose"], ["mouth", "Mouth"]];
    const subPanes = {};
    for (const [id, label] of subs) {
      const b = h("button", { type: "button", "aria-pressed": String(faceTab === id),
        onclick: () => {
          faceTab = id;
          for (const [oid] of subs) {
            subPanes[oid].style.display = oid === id ? "" : "none";
            subBar.children[subs.findIndex((s) => s[0] === oid)].setAttribute("aria-pressed", String(oid === id));
          }
        } }, label);
      subBar.appendChild(b);
      subPanes[id] = h("div", { class: "ce-section", style: faceTab === id ? "" : "display:none" });
    }
    pane.appendChild(subBar);

    const FL = LIMITS.face;

    subPanes.eyes.append(
      segmented(EYE_SHAPES.map((v) => ({ value: v, label: titleCase(v) })),
        (s) => s.face.eyeShape, (s, v) => { s.face.eyeShape = v; }),
      slider("Roundness", FL.eyeRoundness, (s) => s.face.eyeRoundness, (s, v) => { s.face.eyeRoundness = v; }),
      slider("Width", FL.width, (s) => s.face.width, (s, v) => { s.face.width = v; }),
      slider("Height", FL.height, (s) => s.face.height, (s, v) => { s.face.height = v; }),
      slider("Gap", FL.gap, (s) => s.face.gap, (s, v) => { s.face.gap = v; }),
      slider("Rotation (overall)", FL.rotation, (s) => s.face.rotation, (s, v) => { s.face.rotation = v; }),
      slider("Left tilt", FL.leftEyeRotation, (s) => s.face.leftEyeRotation, (s, v) => { s.face.leftEyeRotation = v; }),
      slider("Right tilt", FL.rightEyeRotation, (s) => s.face.rightEyeRotation, (s, v) => { s.face.rightEyeRotation = v; }),
      toggle("Eye highlights", (s) => s.face.eyeHighlight.enabled, (s, v) => { s.face.eyeHighlight.enabled = v; }),
      slider("Highlight size", { min: 0, max: 100, step: 1, unit: "%" },
        (s) => s.face.eyeHighlight.size, (s, v) => { s.face.eyeHighlight.size = v; }),
      slider("Highlight X", { min: -100, max: 100, step: 1, unit: "" },
        (s) => s.face.eyeHighlight.offsetX, (s, v) => { s.face.eyeHighlight.offsetX = v; }),
      slider("Highlight Y", { min: -100, max: 100, step: 1, unit: "" },
        (s) => s.face.eyeHighlight.offsetY, (s, v) => { s.face.eyeHighlight.offsetY = v; }),
    );

    subPanes.nose.append(
      toggle("Show nose", (s) => s.face.noseEnabled, (s, v) => { s.face.noseEnabled = v; }),
      segmented(NOSE_SHAPES.map((v) => ({ value: v, label: titleCase(v) })),
        (s) => s.face.noseShape, (s, v) => { s.face.noseShape = v; s.face.noseEnabled = true; }),
      slider("Width", FL.noseWidth, (s) => s.face.noseWidth, (s, v) => { s.face.noseWidth = v; }),
      slider("Height", FL.noseHeight, (s) => s.face.noseHeight, (s, v) => { s.face.noseHeight = v; }),
      slider("Position", FL.noseY, (s) => s.face.noseY, (s, v) => { s.face.noseY = v; }),
      slider("Rotation", FL.noseRotation, (s) => s.face.noseRotation, (s, v) => { s.face.noseRotation = v; }),
    );

    subPanes.mouth.append(
      toggle("Show mouth", (s) => s.face.mouthEnabled, (s, v) => { s.face.mouthEnabled = v; }),
      segmented(MOUTH_SHAPES.filter((m) => m !== "none").map((v) => ({ value: v, label: titleCase(v) })),
        (s) => s.face.mouthShape, (s, v) => { s.face.mouthShape = v; s.face.mouthEnabled = true; }),
      slider("Width", FL.mouthWidth, (s) => s.face.mouthWidth, (s, v) => { s.face.mouthWidth = v; }),
      slider("Height", FL.mouthHeight, (s) => s.face.mouthHeight, (s, v) => { s.face.mouthHeight = v; }),
      slider("Curve", FL.mouthCurve, (s) => s.face.mouthCurve, (s, v) => { s.face.mouthCurve = v; }),
      slider("Position", FL.mouthY, (s) => s.face.mouthY, (s, v) => { s.face.mouthY = v; }),
      slider("Rotation", FL.mouthRotation, (s) => s.face.mouthRotation, (s, v) => { s.face.mouthRotation = v; }),
    );

    for (const [id] of subs) pane.appendChild(subPanes[id]);

    pane.appendChild(h("div", { class: "ce-section" }, [
      legend("Placement", ICON.parts),
      toggle("Show a face", (s) => s.face.enabled, (s, v) => { s.face.enabled = v; }),
      slider("Across", FL.offsetX, (s) => s.face.offsetX, (s, v) => { s.face.offsetX = v; }),
      slider("Up and down", FL.offsetY, (s) => s.face.offsetY, (s, v) => { s.face.offsetY = v; }),
      h("p", { class: "ce-note" }, "The face is pinned to one part. Choose which in Body."),
    ]));
  }

  // ── body tab ─────────────────────────────────────────────────────────────

  function buildBodyTab(pane) {
    pane.appendChild(h("div", { class: "ce-section" }, [
      legend("Body", ICON.parts),
      thumbGrid("ce-grid-6", PRESETS.map((p) => ({ id: p.id, name: p.name })), {
        signature: (d) => d.scene.appearance.paletteId,
        doc: (d, it) => applyPreset(cloneScene(d), it.id),
      }, (d, it) => d.scene.entity.preset === it.id, (it) => {
        selectedPart = 0;
        commit(applyPreset(doc, it.id));
      }),
    ]));

    const list = h("div", { class: "ce-partlist" });
    const addBtn = h("button", { type: "button", onclick: () => edit((s) => {
      const src = s.entity.parts[selectedPart] || defaultPart();
      const copy = Object.assign({}, src, {
        id: uniquePartId(s.entity.parts, `${src.shape}`),
        faceHost: false, positionX: src.positionX + 20, positionZ: src.positionZ - 10,
      });
      s.entity.parts.push(copy);
      s.entity.preset = "custom";
      selectedPart = s.entity.parts.length - 1;
    }) }, [icon(ICON.add), "Add part"]);

    syncs.push((d) => {
      const parts = d.scene.entity.parts;
      if (selectedPart >= parts.length) selectedPart = parts.length - 1;
      list.textContent = "";
      const pal = palette(d.scene.appearance.paletteId);
      parts.forEach((p, i) => {
        const row = h("div", {
          class: "ce-part", role: "option", "aria-selected": String(i === selectedPart),
          onclick: () => { selectedPart = i; for (const fn of syncs) fn(doc); },
        }, [
          h("span", { class: "ce-part-dot", style: `background:${p.color || pal.body}` }),
          h("span", { class: "ce-part-name" }, `${SHAPE_LABELS[p.shape] || p.shape}`),
          p.faceHost ? h("span", { class: "ce-part-host" }, "face") : null,
          h("button", { class: "ce-icon-btn", type: "button", title: "Move back",
            onclick: (e) => { e.stopPropagation(); movePart(i, -1); } }, [icon(ICON.up)]),
          h("button", { class: "ce-icon-btn", type: "button", title: "Move forward",
            onclick: (e) => { e.stopPropagation(); movePart(i, 1); } }, [icon(ICON.down)]),
          parts.length > 1 ? h("button", { class: "ce-icon-btn", type: "button", title: "Remove",
            onclick: (e) => { e.stopPropagation(); removePart(i); } }, [icon(ICON.remove)]) : null,
        ]);
        list.appendChild(row);
      });
    });

    pane.appendChild(h("div", { class: "ce-section" }, [legend("Parts", ICON.parts), list, addBtn]));

    const shapeGrid = h("div", { class: "ce-grid ce-grid-5" });
    const shapeBtns = SHAPES.map((sh) => {
      const b = h("button", { class: "ce-chip", type: "button", title: SHAPE_LABELS[sh] || sh,
        "aria-label": SHAPE_LABELS[sh] || sh,
        onclick: () => editPart((p) => { p.shape = sh; }) });
      shapeGrid.appendChild(b);
      return b;
    });
    let shapeSig = null;
    syncs.push((d) => {
      const cur = d.scene.entity.parts[selectedPart];
      const sig = d.scene.appearance.paletteId;
      if (sig !== shapeSig) {
        shapeSig = sig;
        SHAPES.forEach((sh, i) => {
          shapeBtns[i].innerHTML = renderToString(shapeThumb(d, sh), { size: 40, idPrefix: `sh${i}` });
        });
      }
      SHAPES.forEach((sh, i) => shapeBtns[i].setAttribute("aria-pressed", String(!!cur && cur.shape === sh)));
    });

    const PL = LIMITS.part;
    pane.appendChild(h("div", { class: "ce-section" }, [
      legend("Shape", ICON.parts), shapeGrid,
      number("Position X", PL.positionX, (s) => part(s).positionX, (s, v) => { part(s).positionX = v; }),
      number("Position Y", PL.positionY, (s) => part(s).positionY, (s, v) => { part(s).positionY = v; }),
      number("Position Z", PL.positionZ, (s) => part(s).positionZ, (s, v) => { part(s).positionZ = v; }),
      slider("Width", PL.width, (s) => part(s).width, (s, v) => { part(s).width = v; }),
      slider("Height", PL.height, (s) => part(s).height, (s, v) => { part(s).height = v; }),
      slider("Depth", PL.depth, (s) => part(s).depth, (s, v) => { part(s).depth = v; }),
      number("Rotation X", PL.rotationX, (s) => part(s).rotationX, (s, v) => { part(s).rotationX = v; }),
      number("Rotation Y", PL.rotationY, (s) => part(s).rotationY, (s, v) => { part(s).rotationY = v; }),
      number("Rotation Z", PL.rotationZ, (s) => part(s).rotationZ, (s, v) => { part(s).rotationZ = v; }),
      slider("Corner rounding", PL.round, (s) => part(s).round, (s, v) => { part(s).round = v; }),
      slider("Taper", PL.taper, (s) => part(s).taper, (s, v) => { part(s).taper = v; }),
      slider("Shade", PL.shade, (s) => part(s).shade, (s, v) => { part(s).shade = v; }),
      colour("Part colour", (s) => part(s).color, (s, v) => { part(s).color = v; },
        (d) => palette(d.scene.appearance.paletteId).body),
      toggle("Outline this part", (s) => part(s).outline !== false, (s, v) => { part(s).outline = v; }),
      toggle("The face goes here", (s) => !!part(s).faceHost, (s, v) => {
        if (!v) return;
        s.entity.parts.forEach((p, i) => { p.faceHost = i === selectedPart; });
      }),
    ]));

    function part(s) {
      return s.entity.parts[selectedPart] || s.entity.parts[0] || defaultPart();
    }
    function editPart(fn) {
      edit((s) => { fn(part(s)); s.entity.preset = "custom"; });
    }
    function movePart(i, dir) {
      const j = i + dir;
      edit((s) => {
        if (j < 0 || j >= s.entity.parts.length) return;
        const [p] = s.entity.parts.splice(i, 1);
        s.entity.parts.splice(j, 0, p);
        s.entity.preset = "custom";
        selectedPart = j;
      });
    }
    function removePart(i) {
      edit((s) => {
        if (s.entity.parts.length <= 1) return;
        s.entity.parts.splice(i, 1);
        s.entity.preset = "custom";
        if (selectedPart >= s.entity.parts.length) selectedPart = s.entity.parts.length - 1;
      });
    }
  }

  /**
   * A thumbnail is a document, so it goes through the same renderer.
   *
   * Nothing here draws a special little icon of a shape or a face. Both grids
   * show the real thing at 44px, which means a thumbnail cannot be wrong about
   * what picking it does — and when a primitive or a face changes, its picker
   * changes with it, with no second drawing to remember to update.
   */
  function thumbBase(d) {
    const next = cloneScene(d);
    next.scene.appearance.backgroundStyle = "none";
    next.scene.camera.frame = "none";
    next.scene.camera.padding = 3;
    next.scene.effects.showAvatarShadow = false;
    next.scene.effects.showFaceShadow = false;
    next.scene.view = Object.assign({}, next.scene.view, { scale: 1, positionX: 0, positionY: 0, roll: 0 });
    return next;
  }

  function shapeThumb(d, shape) {
    const next = thumbBase(d);
    next.scene.entity.parts = [defaultPart({ id: "t", shape, faceHost: true, round: 40 })];
    next.scene.face = Object.assign({}, next.scene.face, { enabled: false });
    next.scene.view.yaw = 0.35;
    next.scene.view.pitch = 0.3;
    return next;
  }

  /**
   * A face thumbnail is a close-up, not a shrunken character.
   *
   * At 50px a whole head leaves a few pixels of eye, and twelve of those are
   * indistinguishable — which is what the first version shipped. The fix that
   * suggests itself is to scale the face metrics up, but then the picker is
   * lying: it shows proportions you cannot get by clicking it. So the *camera*
   * moves in instead. The chip crops (overflow: hidden), the face is drawn at
   * exactly the size the preset will give you, and the twelve are told apart at
   * a glance.
   */
  const THUMB_ZOOM = 1.3;

  function faceThumb(d, facePreset) {
    const next = thumbBase(d);
    next.scene.entity.parts = [defaultPart({ id: "t", shape: "rounded-box", round: 74, faceHost: true })];
    next.scene.face = Object.assign({}, next.scene.face, facePreset, { enabled: true, rotation: 0 });
    next.scene.camera.padding = 0;
    next.scene.view.yaw = 0;
    next.scene.view.pitch = 0.05;
    next.scene.view.scale = THUMB_ZOOM;
    return next;
  }

  // ── colour tab ───────────────────────────────────────────────────────────

  function buildColourTab(pane) {
    const swatches = h("div", { class: "ce-swatches" });
    const btns = PALETTES.map((p) => {
      const b = h("button", { class: "ce-swatch", type: "button", title: p.name, "aria-label": p.name,
        style: `background:${p.background}`,
        onclick: () => edit((s) => { s.appearance.paletteId = p.id; }) },
      [h("span", { style: `background:linear-gradient(135deg, ${p.body}, ${p.shade})` })]);
      swatches.appendChild(b);
      return b;
    });
    syncs.push((d) => {
      PALETTES.forEach((p, i) => btns[i].setAttribute("aria-pressed", String(p.id === d.scene.appearance.paletteId)));
    });

    const EL = LIMITS.effects;
    pane.append(
      h("div", { class: "ce-section" }, [legend("Palette", ICON.colour), swatches]),
      h("div", { class: "ce-section" }, [
        legend("Background", ICON.colour),
        segmented(BACKGROUND_STYLES.map((v) => ({ value: v, label: titleCase(v) })),
          (s) => s.appearance.backgroundStyle, (s, v) => { s.appearance.backgroundStyle = v; }),
        colour("Background colour", (s) => s.appearance.background, (s, v) => { s.appearance.background = v; },
          (d) => palette(d.scene.appearance.paletteId).background),
      ]),
      h("div", { class: "ce-section" }, [
        legend("Ink", ICON.colour),
        colour("Outline", (s) => s.effects.outline.color, (s, v) => { s.effects.outline.color = v; },
          (d) => palette(d.scene.appearance.paletteId).outline),
        colour("Face", (s) => s.face.color, (s, v) => { s.face.color = v; },
          (d) => palette(d.scene.appearance.paletteId).face),
      ]),
      h("div", { class: "ce-section" }, [
        legend("Grade", ICON.effects),
        slider("Brightness", EL.brightness, (s) => s.effects.colorGrade.brightness, (s, v) => { s.effects.colorGrade.brightness = v; }),
        slider("Saturation", EL.saturation, (s) => s.effects.colorGrade.saturation, (s, v) => { s.effects.colorGrade.saturation = v; }),
        slider("Tint strength", EL.tintAmount, (s) => s.effects.colorGrade.tintAmount, (s, v) => { s.effects.colorGrade.tintAmount = v; }),
        colour("Tint", (s) => rgbToHex(s.effects.colorGrade.tintR, s.effects.colorGrade.tintG, s.effects.colorGrade.tintB),
          (s, v) => {
            const [r, g, b] = hexToRgb(v || "#000000");
            s.effects.colorGrade.tintR = r; s.effects.colorGrade.tintG = g; s.effects.colorGrade.tintB = b;
          }, () => "#000000"),
      ]),
    );
  }

  // ── effects tab ──────────────────────────────────────────────────────────

  function buildEffectsTab(pane) {
    const EL = LIMITS.effects;
    const FOL = LIMITS.follow;
    const LL = LIMITS.lighting;
    const VL = LIMITS.view;

    const nameInput = h("input", { class: "ce-text", type: "text", maxlength: "80", "aria-label": "Name" });
    nameInput.addEventListener("input", () => edit((s, d) => { d.metadata.name = nameInput.value; }, true));
    syncs.push((d) => { if (document.activeElement !== nameInput) nameInput.value = d.metadata.name; });

    pane.append(
      h("div", { class: "ce-section" }, [legend("Name", ICON.star), nameInput]),

      h("div", { class: "ce-section" }, [
        legend("Outline", ICON.effects),
        toggle("Show outline", (s) => s.effects.showOutline, (s, v) => { s.effects.showOutline = v; }),
        slider("Width", EL.outlineWidth, (s) => s.effects.outline.width, (s, v) => { s.effects.outline.width = v; }),
        slider("Opacity", EL.opacity, (s) => s.effects.outline.opacity, (s, v) => { s.effects.outline.opacity = v; }),
        toggle("Show seams between parts", (s) => s.effects.seams, (s, v) => { s.effects.seams = v; }),
        h("p", { class: "ce-note" }, "Seams off draws one outline around the whole character instead of one per part."),
      ]),

      h("div", { class: "ce-section" }, [
        legend("Shadow", ICON.effects),
        toggle("Drop shadow", (s) => s.effects.showAvatarShadow, (s, v) => { s.effects.showAvatarShadow = v; }),
        slider("Direction", EL.direction, (s) => s.effects.avatarShadow.direction, (s, v) => { s.effects.avatarShadow.direction = v; }),
        slider("Distance", EL.distance, (s) => s.effects.avatarShadow.distance, (s, v) => { s.effects.avatarShadow.distance = v; }),
        slider("Softness", EL.softness, (s) => s.effects.avatarShadow.softness, (s, v) => { s.effects.avatarShadow.softness = v; }),
        slider("Opacity", EL.opacity, (s) => s.effects.avatarShadow.opacity, (s, v) => { s.effects.avatarShadow.opacity = v; }),
        toggle("Face shadow", (s) => s.effects.showFaceShadow, (s, v) => { s.effects.showFaceShadow = v; }),
      ]),

      h("div", { class: "ce-section" }, [
        legend("Light", ICON.effects),
        toggle("Directional light", (s) => s.lighting.enabled, (s, v) => { s.lighting.enabled = v; }),
        slider("Azimuth", LL.azimuth, (s) => s.lighting.azimuth, (s, v) => { s.lighting.azimuth = v; }),
        slider("Elevation", LL.elevation, (s) => s.lighting.elevation, (s, v) => { s.lighting.elevation = v; }),
        slider("Strength", LL.strength, (s) => s.lighting.strength, (s, v) => { s.lighting.strength = v; }),
      ]),

      h("div", { class: "ce-section" }, [
        legend("Follow cursor", ICON.face),
        toggle("Follow the cursor", (s) => s.follow.enabled, (s, v) => { s.follow.enabled = v; }),
        segmented([{ value: "element", label: "Near the avatar" }, { value: "window", label: "Whole window" }],
          (s) => s.follow.scope, (s, v) => { s.follow.scope = v; }),
        slider("Turn left and right", FOL.yawRange, (s) => s.follow.yawRange, (s, v) => { s.follow.yawRange = v; }),
        slider("Turn up and down", FOL.pitchRange, (s) => s.follow.pitchRange, (s, v) => { s.follow.pitchRange = v; }),
        slider("Eye travel", FOL.eyeShift, (s) => s.follow.eyeShift, (s, v) => { s.follow.eyeShift = v; }),
        slider("Snappiness", FOL.stiffness, (s) => s.follow.stiffness, (s, v) => { s.follow.stiffness = v; }),
        slider("Settle", FOL.damping, (s) => s.follow.damping, (s, v) => { s.follow.damping = v; }),
        toggle("Blink", (s) => s.follow.blink, (s, v) => { s.follow.blink = v; }),
        h("p", { class: "ce-note" }, "Motion is switched off automatically when the system asks for reduced motion."),
      ]),

      h("div", { class: "ce-section" }, [
        legend("Frame", ICON.parts),
        segmented(FRAMES.map((v) => ({ value: v, label: titleCase(v) })),
          (s) => s.camera.frame, (s, v) => { s.camera.frame = v; }),
        number("Export size", { min: 16, max: 2048, step: 16, unit: "px" },
          (s) => s.camera.size, (s, v) => { s.camera.size = v; }),
      ]),

      h("div", { class: "ce-section" }, [
        legend("Camera", ICON.effects),
        slider("Turn", VL.yaw, (s) => s.view.yaw, (s, v) => { s.view.yaw = v; }),
        slider("Tilt", VL.pitch, (s) => s.view.pitch, (s, v) => { s.view.pitch = v; }),
        slider("Roll", VL.roll, (s) => s.view.roll, (s, v) => { s.view.roll = v; }),
        slider("Zoom", VL.scale, (s) => s.view.scale, (s, v) => { s.view.scale = v; }),
        slider("Across", VL.positionX, (s) => s.view.positionX, (s, v) => { s.view.positionX = v; }),
        slider("Up and down", VL.positionY, (s) => s.view.positionY, (s, v) => { s.view.positionY = v; }),
        h("p", { class: "ce-note" }, "Drag the preview to turn the character."),
      ]),
    );
  }

  // ── saved presets ────────────────────────────────────────────────────────

  function buildSavedPresets() {
    const key = opts.storageKey === undefined ? "character.presets" : opts.storageKey;
    const row = h("div", { class: "ce-grid ce-grid-6" });
    const section = h("div", { class: "ce-section" }, [
      h("div", { class: "ce-row" }, [
        legend("Saved", ICON.star),
        h("button", { class: "ce-icon-btn", type: "button", title: "Save this character",
          onclick: () => { save(); render(); say("Saved"); } }, [icon(ICON.add)]),
      ]),
      row,
    ]);

    const read = () => {
      try {
        const raw = localStorage.getItem(key);
        const list = raw ? JSON.parse(raw) : [];
        return Array.isArray(list) ? list.slice(0, 12) : [];
      } catch {
        // Private browsing, a disabled storage policy, or a corrupt value. A
        // missing saved row is a missing convenience; a thrown error here would
        // take the whole editor down with it.
        return [];
      }
    };
    const write = (list) => {
      try { localStorage.setItem(key, JSON.stringify(list.slice(0, 12))); } catch { /* storage unavailable */ }
    };
    const save = () => {
      const list = read();
      const payload = encode(doc);
      write([payload].concat(list.filter((p) => p !== payload)));
    };

    function render() {
      row.textContent = "";
      const list = read();
      if (!list.length) {
        row.appendChild(h("p", { class: "ce-empty", style: "grid-column:1/-1" },
          "Characters you save appear here."));
        return;
      }
      list.forEach((payload, i) => {
        const saved = decode(payload);
        if (!saved) return;
        const b = h("button", { class: "ce-chip", type: "button", title: saved.metadata.name,
          "aria-label": saved.metadata.name,
          onclick: () => commit(saved),
          oncontextmenu: (e) => {
            e.preventDefault();
            write(read().filter((_, j) => j !== i));
            render();
            say("Removed");
          } });
        b.innerHTML = renderToString(saved, { size: 44, idPrefix: `sv${i}` });
        row.appendChild(b);
      });
    }
    render();
    return section;
  }

  // ── first paint and the public handle ────────────────────────────────────

  for (const fn of syncs) fn(doc);

  return {
    get element() { return root; },
    getDocument() { return cloneScene(doc); },
    setDocument(next) { commit(next); },
    on(event, fn) {
      if (listeners[event]) listeners[event].push(fn);
      return () => { listeners[event] = listeners[event].filter((f) => f !== fn); };
    },
    destroy() {
      if (instance) instance.destroy();
      if (root.parentNode) root.parentNode.removeChild(root);
      syncs.length = 0;
      listeners.change.length = 0;
    },
  };
}

function uniquePartId(parts, base) {
  const used = new Set(parts.map((p) => p.id));
  let i = 1;
  let id = base;
  while (used.has(id)) id = `${base}-${++i}`;
  return id;
}

function faceMatches(face, sample) {
  return Object.keys(sample).every((k) => {
    const a = face[k], b = sample[k];
    return typeof b === "object" && b ? true : a === b;
  });
}

/**
 * The face presets row.
 *
 * Twelve, not fifty. These are meant to be scanned in one glance and picked
 * from, with the sliders underneath for anything in between — a wall of
 * near-identical faces is a wall, not a choice.
 */
const FACE_PRESETS = [
  { name: "Wide awake", face: { eyeShape: "rounded", eyeRoundness: 100, width: 28, height: 64, gap: 41, mouthEnabled: false, noseEnabled: false, leftEyeRotation: 0, rightEyeRotation: 0 } },
  { name: "Content", face: { eyeShape: "rounded", eyeRoundness: 100, width: 24, height: 38, gap: 44, mouthEnabled: true, mouthShape: "curve", mouthCurve: 55, mouthWidth: 44, mouthY: 54, noseEnabled: false } },
  { name: "Flat", face: { eyeShape: "line", width: 26, height: 30, gap: 44, mouthEnabled: true, mouthShape: "line", mouthWidth: 34, mouthY: 50, noseEnabled: false } },
  { name: "Dot", face: { eyeShape: "ellipse", width: 14, height: 14, gap: 50, mouthEnabled: false, noseEnabled: false, leftEyeRotation: 0, rightEyeRotation: 0 } },
  { name: "Wink", face: { eyeShape: "rounded", eyeRoundness: 100, width: 22, height: 48, gap: 42, leftEyeRotation: -16, rightEyeRotation: 16, mouthEnabled: false, noseEnabled: false } },
  { name: "Kitten", face: { eyeShape: "ellipse", width: 18, height: 42, gap: 40, noseEnabled: true, noseShape: "oval", noseWidth: 9, noseHeight: 7, noseY: 34, mouthEnabled: false } },
  { name: "Smile", face: { eyeShape: "ellipse", width: 16, height: 20, gap: 46, mouthEnabled: true, mouthShape: "curve", mouthCurve: 70, mouthWidth: 50, mouthY: 50, noseEnabled: false } },
  { name: "Whiskers", face: { eyeShape: "ellipse", width: 16, height: 26, gap: 46, mouthEnabled: true, mouthShape: "cat", mouthWidth: 40, mouthHeight: 14, mouthY: 48, noseEnabled: true, noseShape: "inverted-triangle", noseWidth: 11, noseHeight: 8, noseY: 32 } },
  { name: "Sleepy", face: { eyeShape: "line", width: 30, height: 40, gap: 40, mouthEnabled: true, mouthShape: "oval", mouthWidth: 16, mouthHeight: 20, mouthY: 52, noseEnabled: false } },
  { name: "Leafy", face: { eyeShape: "leaf", width: 24, height: 30, gap: 42, leftEyeRotation: -10, rightEyeRotation: 10, mouthEnabled: false, noseEnabled: false } },
  { name: "Square", face: { eyeShape: "rounded", eyeRoundness: 25, width: 24, height: 24, gap: 46, mouthEnabled: true, mouthShape: "line", mouthWidth: 46, mouthHeight: 8, mouthY: 44, noseEnabled: false } },
  { name: "Tall", face: { eyeShape: "rounded", eyeRoundness: 100, width: 18, height: 72, gap: 36, mouthEnabled: false, noseEnabled: false } },
];

export { FACE_PRESETS };
