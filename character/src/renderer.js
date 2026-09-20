/**
 * A character, alive in the DOM.
 *
 * One `<svg>` element per instance, for the life of the instance. Nothing here
 * ever replaces the node: doing so breaks CSS transitions on it, drops focus,
 * and leaves the IntersectionObserver in `follow.js` watching a detached
 * element.
 *
 * Inside it, updates take one of two paths. A frame that only changes *numbers*
 * — which is every frame of a cursor following, a blink, or a drag — writes
 * attributes onto nodes that already exist. A frame that changes the *shape* of
 * the drawing (a part added, the face switched off) rebuilds the subtree. The
 * split is what makes the motion smooth: the projection costs a tenth of a
 * millisecond, while re-parsing an SVG subtree sixty times a second costs
 * enough to be felt.
 *
 * The instance owns three things and no more: the document, the pose, and the
 * element. Pose is not stored in the document — a head turned toward the cursor
 * is not an edit, and writing it back would mean every saved character carried
 * whatever angle it happened to be at when the page was closed.
 */

import { normalize, cloneScene } from "./schema.js";
import { buildRenderModel } from "./project.js";
import { toSVGBody, toSVG, SVG_NS } from "./svg.js";
import { FollowController } from "./follow.js";
import { hashString, clamp } from "./math.js";

/** Two decimals, for attributes written sixty times a second. */
const round = (n) => Math.round(n * 100) / 100;

let uid = 0;

/**
 * Mount a character into `target`.
 *
 * `target` may be an element to render into, or an existing `<svg>` to take
 * over. Returns the instance; call `destroy()` when the host unmounts it, or
 * the follow loop keeps a reference to an element nobody can see.
 */
export function createCharacter(target, document_, options) {
  const opts = options || {};
  const host = typeof target === "string"
    ? (typeof document !== "undefined" ? document.querySelector(target) : null)
    : target;
  if (!host) throw new Error("createCharacter: target element not found");

  const doc = normalize(document_);
  const id = `ch${++uid}`;
  const isSvg = host.namespaceURI === SVG_NS && host.tagName.toLowerCase() === "svg";
  const el = isSvg ? host : document.createElementNS(SVG_NS, "svg");

  const state = {
    doc,
    pose: { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0, blink: 0 },
    size: opts.size || doc.scene.camera.size,
    title: opts.title,
    model: null,
  };

  function applyAttributes() {
    el.setAttribute("viewBox", "0 0 256 256");
    el.setAttribute("role", "img");
    const label = state.title == null ? state.doc.metadata.name : state.title;
    if (label) el.setAttribute("aria-label", label); else el.removeAttribute("aria-label");
    if (opts.responsive === false) {
      el.setAttribute("width", String(state.size));
      el.setAttribute("height", String(state.size));
    } else {
      // The default is a character that fills whatever box CSS gives it. A
      // fixed pixel size inside a flex rail is the reason avatars stop lining
      // up the moment someone changes a row height.
      el.removeAttribute("width");
      el.removeAttribute("height");
      el.style.display = el.style.display || "block";
      el.style.width = "100%";
      el.style.height = "100%";
    }
  }

  /**
   * What must be true for a frame to be patchable rather than rebuilt.
   *
   * Anything that changes the *shape* of the DOM — a part added, the outline
   * switched off, the face turned away — goes in here. Everything else is a
   * number that can be written onto a node that already exists.
   */
  function shapeOf(m) {
    const f = m.face;
    return [
      m.parts.length, m.seams, !!m.outline, !!m.shadow, !!m.faceShadow,
      m.background.style, m.frame.kind,
      f ? `${f.eyes.length}|${f.highlights.length}|${!!f.mouth}|${!!f.nose}` : "-",
    ].join(",");
  }

  let nodes = null;
  let shape = null;

  function rebuild(model) {
    const { defs, content } = toSVGBody(model, { idPrefix: id, hooks: true });
    el.innerHTML = defs + content;
    const pick = (kind) => Array.from(el.querySelectorAll(`[data-ch="${kind}"]`));
    nodes = {
      parts: pick("p"), seams: pick("s"), face: pick("f")[0] || null,
      eyes: pick("e"), highlights: pick("h"), mouth: pick("m")[0] || null,
      nose: pick("n")[0] || null,
    };
    shape = shapeOf(model);
  }

  /**
   * Write one frame onto the nodes that are already there.
   *
   * **This handles a change of POSE and nothing else.** It writes the part
   * outlines, the fills (which move with the light), the face's transform, and
   * the eyes — because those are exactly what turning a head changes. It does
   * not write the mouth's path, the nose's, the ink colour, the background, or
   * the outline weight, because a pose cannot change any of them.
   *
   * That invariant is load-bearing, and it was learned the hard way: this used
   * to run for *every* update, including document edits, so picking a different
   * mouth in the editor changed the document, re-projected the character, and
   * then wrote only the things a pose can change — leaving the old mouth on
   * screen. The control worked, the render was correct, and nothing moved.
   *
   * So `draw()` decides: a pose patches, anything else rebuilds. Rebuilding on
   * an edit is affordable — it was measured at a locked 60fps even when every
   * frame rebuilt — and it cannot silently miss a field the way enumerating
   * them here can.
   */
  function patch(model) {
    for (let i = 0; i < nodes.parts.length; i++) {
      const p = model.parts[i];
      if (!p) break;
      nodes.parts[i].setAttribute("d", p.d);
      nodes.parts[i].setAttribute("fill", p.fill);
    }
    for (let i = 0; i < nodes.seams.length; i++) {
      if (model.parts[i]) nodes.seams[i].setAttribute("d", model.parts[i].d);
    }

    const f = model.face;
    if (!f || !nodes.face) return;
    nodes.face.setAttribute("transform", `matrix(${f.matrix.map(round).join(" ")})`);
    nodes.face.setAttribute("opacity", String(round(f.opacity)));

    for (let i = 0; i < nodes.eyes.length; i++) {
      const e = f.eyes[i];
      if (!e) break;
      const t = `translate(${round(e.x)} ${round(e.y)})` + (e.rotation ? ` rotate(${round(e.rotation)})` : "");
      nodes.eyes[i].setAttribute("transform", t);
      const path = nodes.eyes[i].firstChild;
      if (path && path.setAttribute) path.setAttribute("d", e.d);
    }
    for (let i = 0; i < nodes.highlights.length; i++) {
      const h = f.highlights[i];
      if (!h) break;
      nodes.highlights[i].setAttribute("cx", String(round(h.x)));
      nodes.highlights[i].setAttribute("cy", String(round(h.y)));
      nodes.highlights[i].setAttribute("opacity", String(round(f.highlightOpacity * (h.fade == null ? 1 : h.fade))));
    }
  }

  /**
   * Redraw. `poseOnly` is a promise from the caller that nothing about the
   * *document* changed — only where the character is looking.
   */
  function draw(poseOnly) {
    const model = buildRenderModel(state.doc, { pose: state.pose });
    state.model = model;
    if (poseOnly && nodes && shape === shapeOf(model)) patch(model);
    else rebuild(model);
    if (opts.onRender) opts.onRender(model);
  }

  if (!isSvg) {
    if (opts.replace !== false) host.textContent = "";
    host.appendChild(el);
  }
  applyAttributes();
  draw();

  const follow = new FollowController(
    el,
    Object.assign({}, state.doc.scene.follow, opts.follow),
    (pose) => { state.pose = pose; draw(true); },
    opts.seed == null ? hashString(state.doc.metadata.name + id) : opts.seed,
  );

  // Dragging turns the camera, which is the same kind of change as a pose —
  // every field it touches is one `patch()` writes — so it stays on the fast path.
  const drag = opts.draggable ? attachDrag(el, state, () => draw(true), opts.onChange) : null;

  return {
    el,
    get document() { return cloneScene(state.doc); },
    get model() { return state.model; },

    /** Swap in a new document. Pose and follow settings carry over. */
    setDocument(next) {
      state.doc = normalize(next);
      follow.configure(Object.assign({}, state.doc.scene.follow, opts.follow));
      applyAttributes();
      draw();
    },

    /** Override follow behaviour without touching the document. */
    setFollow(cfg) {
      follow.configure(Object.assign({}, state.doc.scene.follow, cfg));
    },

    /** Drive the pose by hand — for a host that has its own animation source. */
    setPose(pose) {
      state.pose = Object.assign({ yaw: 0, pitch: 0, eyeX: 0, eyeY: 0, blink: 0 }, pose);
      draw(true);
    },

    setSize(px) {
      state.size = px;
      applyAttributes();
    },

    /** The character as a standalone SVG file, at whatever size you ask for. */
    toSVGString(size) {
      return toSVG(buildRenderModel(state.doc), { size: size || state.size, title: state.title });
    },

    destroy() {
      follow.destroy();
      if (drag) drag();
      if (!isSvg && el.parentNode) el.parentNode.removeChild(el);
    },
  };
}

/**
 * Drag to turn the character, for the editor preview.
 *
 * Pointer capture rather than window listeners: a drag that leaves the element
 * must keep turning the head, and must stop when the button is released over
 * some other part of the page — which window listeners get wrong in exactly one
 * direction each.
 */
function attachDrag(el, state, draw, onChange) {
  let active = null;
  el.style.touchAction = "none";
  el.style.cursor = "grab";

  const down = (e) => {
    active = { x: e.clientX, y: e.clientY, yaw: state.doc.scene.view.yaw, pitch: state.doc.scene.view.pitch };
    el.setPointerCapture(e.pointerId);
    el.style.cursor = "grabbing";
  };
  const move = (e) => {
    if (!active) return;
    const v = state.doc.scene.view;
    v.yaw = clamp(active.yaw + (e.clientX - active.x) * 0.006, -1.2, 1.2);
    v.pitch = clamp(active.pitch - (e.clientY - active.y) * 0.006, -1.0, 1.0);
    draw();
  };
  const up = (e) => {
    if (!active) return;
    active = null;
    el.style.cursor = "grab";
    try { el.releasePointerCapture(e.pointerId); } catch { /* capture already gone */ }
    // The document only changes on release. Firing per pointer move would put
    // several hundred entries in a host's undo stack for one drag.
    if (onChange) onChange(cloneScene(state.doc));
  };

  el.addEventListener("pointerdown", down);
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);

  return () => {
    el.removeEventListener("pointerdown", down);
    el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up);
    el.removeEventListener("pointercancel", up);
  };
}

/**
 * Every string this function has ever produced, counted.
 *
 * The id prefix has to be unique per *call*, not per document — and that
 * distinction cost a real bug. It was derived from a hash of the document,
 * which sounds better: identical input, identical output, byte for byte.
 * But SVG ids are document-global, and rendering the same character twice on
 * one page is the common case, not the edge case — an agent appears in the rail
 * and again in a roster. Two `<filter id="s727496-shadow">` in one page means
 * `url(#s727496-shadow)` in the second SVG resolves to the *first* one's defs,
 * and a filter reference that lands outside its own tree makes the element not
 * render at all. The avatar was there, correct, and invisible.
 *
 * Pass `idPrefix` explicitly when reproducible bytes matter — an export, a
 * snapshot, a cache key. In a page, let it count.
 */
let _stringUid = 0;

/**
 * A character as an SVG string, with no DOM involved.
 *
 * This is the one to reach for when a character is decoration rather than a
 * participant — a list of thirty agents, an email, a static export. It costs
 * one projection pass and produces something the browser can cache as an image,
 * instead of thirty live instances competing for frames.
 */
export function renderToString(document_, options) {
  const opts = options || {};
  const doc = normalize(document_);
  const model = buildRenderModel(doc, { pose: opts.pose });
  return toSVG(model, {
    size: opts.size || doc.scene.camera.size,
    idPrefix: opts.idPrefix || `s${hashString(JSON.stringify(doc)) % 1e6}-${++_stringUid}`,
    title: opts.title == null ? doc.metadata.name : opts.title,
  });
}
