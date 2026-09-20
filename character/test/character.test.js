/**
 * The suite.
 *
 * Node only, no DOM, no browser. Everything load-bearing in this package is a
 * pure function of a document, which is what makes that possible — and the fact
 * that it *is* possible is the design working. The one thing a browser is
 * genuinely needed for is `test/follow-browser.html`, and that is a smoke test,
 * not where the follow behaviour is actually asserted: the maths is asserted
 * here, by stepping the controller by hand with a fake clock.
 *
 * The bias throughout is toward properties rather than golden values. "The
 * character fits in the frame at every angle" survives a deliberate change to
 * the geometry; a checked-in path string does not, and a golden file that gets
 * re-baselined on every change is a file that tests nothing.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  defaultScene, defaultPart, normalize, migrate, cloneScene, LIMITS,
} from "../src/index.js";
import { encode, decode, fromURL, toURL } from "../src/codec.js";
import { buildRenderModel } from "../src/project.js";
import { toSVG } from "../src/svg.js";
import { renderToString } from "../src/renderer.js";
import { generateScene } from "../src/generate.js";
import { PRESETS, applyPreset } from "../src/presets.js";
import { PALETTES, contrast, readableInk } from "../src/palettes.js";
import { SHAPES } from "../src/primitives.js";
import { convexHull, simplify, ringToPath } from "../src/hull.js";
import { FollowController } from "../src/follow.js";

// ── schema ─────────────────────────────────────────────────────────────────

test("normalize survives anything and always returns a renderable document", () => {
  for (const junk of [null, undefined, 0, "", [], "not a document", { scene: 7 },
    { scene: { entity: { parts: "nope" } } }, { scene: { view: { yaw: NaN } } }]) {
    const doc = normalize(junk);
    assert.equal(doc.schema, "character.scene");
    assert.ok(doc.scene.entity.parts.length >= 1, "a document always has a body");
    assert.ok(Number.isFinite(doc.scene.view.yaw));
    assert.doesNotThrow(() => renderToString(doc));
  }
});

test("normalize clamps every value into its own published limit", () => {
  const doc = normalize({
    scene: {
      view: { yaw: 99, pitch: -99, scale: 1000 },
      entity: { parts: [{ shape: "sphere", width: 9999, rotationX: 5000 }] },
      face: { gap: -400, mouthWidth: 1e9 },
      effects: { outline: { width: 900, opacity: 900 } },
    },
  });
  assert.equal(doc.scene.view.yaw, LIMITS.view.yaw.max);
  assert.equal(doc.scene.view.pitch, LIMITS.view.pitch.min);
  assert.equal(doc.scene.entity.parts[0].width, LIMITS.part.width.max);
  assert.equal(doc.scene.face.gap, LIMITS.face.gap.min);
  assert.equal(doc.scene.effects.outline.opacity, 100);
});

test("exactly one part hosts the face, however the document arrives", () => {
  const none = normalize({ scene: { entity: { parts: [{ shape: "sphere" }, { shape: "box" }] } } });
  assert.equal(none.scene.entity.parts.filter((p) => p.faceHost).length, 1);

  const many = normalize({
    scene: { entity: { parts: [{ shape: "sphere", faceHost: true }, { shape: "box", faceHost: true }] } },
  });
  assert.equal(many.scene.entity.parts.filter((p) => p.faceHost).length, 1);
});

test("fields this version does not know about survive a round trip", () => {
  const doc = normalize(defaultScene());
  doc.scene.somethingNewer = { kind: "decal", value: 7 };
  doc.scene.entity.parts[0].futureField = "keep me";
  const back = normalize(JSON.parse(JSON.stringify(doc)));
  assert.deepEqual(back.scene.somethingNewer, { kind: "decal", value: 7 });
  assert.equal(back.scene.entity.parts[0].futureField, "keep me");
});

// ── codec ──────────────────────────────────────────────────────────────────

test("a document round-trips through the URL payload", () => {
  const doc = generateScene("round-trip");
  const back = decode(encode(doc));
  assert.deepEqual(back, doc);
});

test("decode refuses junk instead of throwing", () => {
  for (const junk of ["", "!!!!", "eyJ", null, undefined, 42, "aGVsbG8="]) {
    assert.equal(decode(junk), null);
  }
});

test("a payload survives base64url being rewritten to plain base64", () => {
  const doc = generateScene("chat client rewrote my link");
  const payload = encode(doc);
  const rewritten = payload.replace(/-/g, "+").replace(/_/g, "/");
  assert.deepEqual(decode(rewritten), doc);
});

test("fromURL reads our key and the reference builder's", () => {
  const doc = generateScene("from-url");
  assert.deepEqual(fromURL(toURL(doc, "https://example.test/build")), doc);
  assert.deepEqual(fromURL(`https://example.test/x?d=${encode(doc)}`), doc);
  assert.equal(fromURL("https://example.test/x"), null);
});

// ── import from the reference format ───────────────────────────────────────

test("an oneworks.avatar document imports with a body and its own crop", () => {
  const imported = normalize(migrate({
    schema: "oneworks.avatar",
    version: 1,
    metadata: { name: "Avatar" },
    scene: {
      appearance: { bodyShape: "capsule", paletteId: "coral", backgroundStyle: "solid" },
      camera: { background: "#ffc1a9", frame: "rounded", size: 256 },
      entity: { parts: [], preset: "custom" },
      face: { eyeShape: "rounded", width: 28, height: 64, gap: 41, mouthEnabled: false },
      view: { yaw: 0.08, pitch: 0.3, roll: 0.01, scale: 1.28, positionX: 0, positionY: 0 },
    },
  }));

  assert.equal(imported.schema, "character.scene");
  assert.equal(imported.scene.entity.parts.length, 1, "an empty part list becomes a body");
  assert.equal(imported.scene.entity.parts[0].shape, "capsule");
  assert.equal(imported.scene.appearance.background, "#ffc1a9", "the card colour moves to the background");
  assert.equal(imported.scene.camera.fit, "none", "its hand-composed crop is preserved");
  assert.equal(imported.scene.view.scale, 1.28);
  assert.doesNotThrow(() => renderToString(imported));
});

test("a document from an unknown producer is passed through, not mangled", () => {
  const alien = { schema: "someone.else", scene: { view: { yaw: 0.5 } } };
  assert.deepEqual(migrate(alien), alien);
});

// ── rendering ──────────────────────────────────────────────────────────────

test("the same document renders byte-identically when the id prefix is pinned", () => {
  const doc = generateScene("determinism");
  const opts = { idPrefix: "fixed" };
  assert.equal(renderToString(doc, opts), renderToString(doc, opts));
  assert.equal(renderToString(doc, opts), renderToString(cloneScene(doc), opts));
});

/**
 * The same character, drawn twice on one page.
 *
 * SVG ids are global to the document, not scoped to their `<svg>`. Two copies
 * of one `<filter id="x">` means the second SVG's `url(#x)` resolves into the
 * first one's defs — and a filter reference that lands outside its own tree
 * makes the element **not render at all**. The avatar is present, correct, and
 * invisible, with nothing in the console.
 *
 * This is why the default id prefix counts rather than hashing the document:
 * an agent shown in a rail and again in a roster is the ordinary case.
 */
test("two renders of one document never share an id", () => {
  const doc = generateScene("collision");
  const ids = (svg) => [...svg.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);
  const a = ids(renderToString(doc));
  const b = ids(renderToString(doc));
  assert.ok(a.length > 0, "this character should define at least one id");
  for (const id of a) {
    assert.ok(!b.includes(id), `id "${id}" appeared in both renders`);
  }
});

test("every id in one render is prefixed, so nothing collides with the host page", () => {
  const doc = generateScene("prefixing");
  const svg = renderToString(doc, { idPrefix: "mine" });
  for (const [, id] of svg.matchAll(/\bid="([^"]+)"/g)) {
    assert.ok(id.startsWith("mine-"), `id "${id}" is not namespaced`);
  }
  // And every reference resolves to an id this same render defined.
  const defined = new Set([...svg.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  for (const [, ref] of svg.matchAll(/url\(#([^)]+)\)/g)) {
    assert.ok(defined.has(ref), `url(#${ref}) has no matching id in this render`);
  }
});

test("every preset and every shape renders without throwing", () => {
  for (const p of PRESETS) {
    const doc = applyPreset(normalize(defaultScene()), p.id);
    const svg = renderToString(doc);
    assert.ok(svg.startsWith("<svg"), `${p.id} produced no SVG`);
    assert.ok(svg.includes("<path"), `${p.id} produced no geometry`);
  }
  for (const shape of SHAPES) {
    const doc = normalize({ scene: { entity: { parts: [defaultPart({ shape, faceHost: true })] } } });
    const model = buildRenderModel(doc);
    assert.ok(model.parts.length === 1, `${shape} produced no silhouette`);
    assert.ok(model.parts[0].d.length > 10, `${shape} produced an empty path`);
  }
});

/**
 * The auto-fit promise, stated as a test: whatever a character is built from
 * and whichever way it is turned, all of it is inside the frame. This is the
 * assertion that a bounding-*sphere* fit buys, and a bounding-box fit could
 * never pass — it is only correct for the angle it was measured at.
 */
test("every preset stays inside the frame at every angle", () => {
  for (const p of PRESETS) {
    const base = applyPreset(normalize(defaultScene()), p.id);
    for (const yaw of [-1.2, -0.6, 0, 0.6, 1.2]) {
      for (const pitch of [-1, -0.4, 0, 0.4, 1]) {
        for (const roll of [-0.8, 0, 0.8]) {
          const doc = cloneScene(base);
          Object.assign(doc.scene.view, { yaw, pitch, roll, scale: 1 });
          const { bounds } = buildRenderModel(doc);
          const where = `${p.id} at yaw ${yaw} pitch ${pitch} roll ${roll}`;
          assert.ok(bounds.minX >= -0.5, `${where}: clipped left (${bounds.minX})`);
          assert.ok(bounds.minY >= -0.5, `${where}: clipped top (${bounds.minY})`);
          assert.ok(bounds.maxX <= 256.5, `${where}: clipped right (${bounds.maxX})`);
          assert.ok(bounds.maxY <= 256.5, `${where}: clipped bottom (${bounds.maxY})`);
        }
      }
    }
  }
});

test("the fit holds still while the character turns", () => {
  const base = applyPreset(normalize(defaultScene()), "rabbit");
  const widths = [];
  for (let yaw = -1; yaw <= 1.0001; yaw += 0.25) {
    const doc = cloneScene(base);
    doc.scene.view.yaw = yaw;
    doc.scene.view.pitch = 0;
    const { bounds } = buildRenderModel(doc);
    widths.push(bounds.maxY - bounds.minY);
  }
  // The fit factor itself is constant — it comes from a bounding sphere — so
  // the only variation left is perspective: parts genuinely move nearer and
  // further as the character turns, and genuinely change size when they do.
  // That is the effect, not a bug. What must not happen is the *frame* being
  // recomputed, which would make the whole character pump on every frame.
  const spread = Math.max(...widths) - Math.min(...widths);
  assert.ok(spread < 4, `the character resized by ${spread.toFixed(2)}px while turning`);
});

test("the face turns away and fades instead of being drawn through the head", () => {
  const doc = normalize(defaultScene());
  doc.scene.view.pitch = 0;
  doc.scene.view.yaw = 0;
  assert.ok(buildRenderModel(doc).face, "the face should be visible head-on");

  doc.scene.view.yaw = Math.PI;   // fully turned away
  assert.equal(buildRenderModel(doc).face, null, "a face on the far side is not drawn");
});

test("a face can be switched off entirely", () => {
  const doc = normalize(defaultScene());
  doc.scene.face.enabled = false;
  const model = buildRenderModel(doc);
  assert.equal(model.face, null);
  assert.ok(model.parts.length > 0, "the body is still there");
});

test("blinking closes the eyes and reopens them", () => {
  const doc = normalize(defaultScene());
  const open = buildRenderModel(doc, { pose: { blink: 0 } });
  const shut = buildRenderModel(doc, { pose: { blink: 1 } });
  assert.notEqual(open.face.eyes[0].d, shut.face.eyes[0].d);
  assert.deepEqual(buildRenderModel(doc, { pose: { blink: 0 } }).face.eyes[0].d, open.face.eyes[0].d);
});

// ── escaping ───────────────────────────────────────────────────────────────

/**
 * A document is not trusted input. It arrives from a pasted URL as often as
 * from a colour picker, and the renderer builds markup by concatenating
 * strings, so this is the one place in the package where getting it wrong is a
 * security bug rather than a rendering one.
 */
test("a hostile name or colour cannot break out of the markup", () => {
  const doc = normalize(defaultScene());
  doc.metadata.name = '"><script>alert(1)</script>';
  doc.scene.appearance.background = '#000" onload="alert(1)';
  const svg = toSVG(buildRenderModel(doc), { title: doc.metadata.name });
  assert.ok(!svg.includes("<script"), "a script tag reached the output");
  // The escaped text `onload=&quot;` is harmless and expected; what must never
  // appear is `onload="`, which would mean a quote escaped its attribute and
  // the next token became markup.
  assert.ok(!svg.includes('onload="'), "an event handler escaped its attribute");
  assert.ok(svg.includes("&lt;script"), "the name should be escaped, not dropped");
  assert.ok(svg.includes("onload=&quot;"), "the hostile value should be escaped, not silently dropped");
});

test("a colour that is not a colour falls back instead of being emitted", () => {
  const doc = normalize({ scene: { appearance: { background: "javascript:alert(1)" } } });
  assert.equal(doc.scene.appearance.background, null);
  assert.ok(!toSVG(buildRenderModel(doc)).includes("javascript:"));
});

// ── generation ─────────────────────────────────────────────────────────────

test("a seed always gives the same character, and different seeds do not", () => {
  assert.deepEqual(generateScene("inbox"), generateScene("inbox"));
  assert.notDeepEqual(generateScene("inbox"), generateScene("launch"));
  // No clock, no randomness: two processes minutes apart must agree, all the
  // way down to the drawn geometry. The id prefix is pinned because it is the
  // one thing that deliberately differs per call — see the collision test.
  const pin = { idPrefix: "p" };
  assert.equal(renderToString(generateScene("stable"), pin), renderToString(generateScene("stable"), pin));
});

test("generated characters are distinguishable from each other", () => {
  const seen = new Map();
  const seeds = Array.from({ length: 200 }, (_, i) => `agent-${i}`);
  for (const seed of seeds) {
    const d = generateScene(seed);
    const key = `${d.scene.entity.preset}|${d.scene.appearance.paletteId}|${d.scene.face.eyeShape}`;
    seen.set(key, (seen.get(key) || 0) + 1);
  }
  // Well over a hundred distinct silhouette-and-colour combinations, so two
  // agents side by side in a rail are very unlikely to collide.
  assert.ok(seen.size > 100, `only ${seen.size} distinct characters in 200 seeds`);
});

test("a generated character's face reads against its own body", () => {
  for (let i = 0; i < 120; i++) {
    const model = buildRenderModel(generateScene(`contrast-${i}`));
    if (!model.face) continue;
    const body = model.parts[model.parts.length - 1].fill;
    assert.ok(contrast(model.face.ink, body) >= 2,
      `seed ${i}: face on body is only ${contrast(model.face.ink, body).toFixed(2)}:1`);
  }
});

test("every palette holds up on its own terms", () => {
  for (const p of PALETTES) {
    assert.ok(contrast(p.face, p.body) >= 3, `${p.id}: face on body`);
    assert.ok(contrast(p.body, p.background) >= 1.2, `${p.id}: body on background`);
    assert.equal(typeof readableInk(p.body), "string");
  }
});

// ── hull ───────────────────────────────────────────────────────────────────

test("the hull keeps the corners and drops the interior", () => {
  const square = [[0, 0], [10, 0], [10, 10], [0, 10]];
  const noise = [[5, 5], [3, 7], [8, 2], [1, 9]];
  const ring = convexHull(square.concat(noise));
  assert.equal(ring.length, 4);
  for (const corner of square) {
    assert.ok(ring.some((p) => p[0] === corner[0] && p[1] === corner[1]), `lost corner ${corner}`);
  }
});

test("simplify collapses a run of collinear points to its ends", () => {
  const edge = Array.from({ length: 20 }, (_, i) => [i, 0]);
  const ring = simplify(edge.concat([[19, 10], [0, 10]]));
  assert.ok(ring.length <= 6, `left ${ring.length} points on two straight edges`);
});

test("a square stays square and a circle stays smooth", () => {
  const square = ringToPath([[0, 0], [10, 0], [10, 10], [0, 10]]);
  assert.ok(!square.includes("C"), "a right angle was splined into a curve");

  const circle = Array.from({ length: 48 }, (_, i) => {
    const a = (i / 48) * Math.PI * 2;
    return [Math.cos(a) * 50, Math.sin(a) * 50];
  });
  const path = ringToPath(circle);
  assert.ok(!path.includes(" L "), "a circle was drawn with straight segments");
});

// ── follow ─────────────────────────────────────────────────────────────────

/**
 * The follow loop, stepped by hand.
 *
 * `step(dt, t)` is deliberately a pure-ish function of the pointer and the
 * elapsed time, so the spring can be driven with a fake clock and asserted on
 * exactly. The browser's job is to call it sixty times a second; that part is
 * three lines and is smoke-tested in `follow-browser.html`. The behaviour worth
 * protecting is here.
 */
function fakeElement(rect) {
  return {
    getBoundingClientRect: () => rect,
    addEventListener() {},
    removeEventListener() {},
  };
}

function settle(controller, seconds = 2) {
  let t = 0;
  for (let i = 0; i < seconds * 60; i++) {
    t += 1000 / 60;
    controller.step(1 / 60, t);
  }
  return controller.pose;
}

/**
 * Where the face ends up on screen for a given pose.
 *
 * The follow tests assert on *this*, not on the sign of `pose.pitch`. Asserting
 * on the number only proves the controller agrees with itself about a
 * convention, and that is exactly how the pitch shipped inverted: the renderer
 * and the controller each did what they said, and the character looked up when
 * you moved the pointer down. The face's position inside its own silhouette is
 * the thing a person actually sees.
 */
function facePosition(pose) {
  const doc = normalize(defaultScene());
  doc.scene.view.yaw = 0;
  doc.scene.view.pitch = 0;
  const m = buildRenderModel(doc, { pose });
  // matrix(a, b, c, d, e, f): e and f are where the face's origin lands.
  return { x: m.face.matrix[4], y: m.face.matrix[5] };
}

test("the head turns toward the pointer, and the drawn face proves it", async () => {
  const { _setPointer } = await import("../src/follow.js");
  const el = fakeElement({ left: 400, top: 300, width: 200, height: 200 });
  const c = new FollowController(el, { enabled: true, yawRange: 40, pitchRange: 24, blink: false }, null, 1);
  const rest = facePosition({});

  _setPointer(1200, 400);            // right of the character
  const right = facePosition(settle(c));
  assert.ok(right.x > rest.x + 2, `pointer right: the face went to ${right.x}, rest was ${rest.x}`);
  assert.ok(c.pose.eyeX > 0, "the eyes should travel with the head");

  _setPointer(-400, 400);            // left of the character
  const left = facePosition(settle(c));
  assert.ok(left.x < rest.x - 2, `pointer left: the face went to ${left.x}, rest was ${rest.x}`);

  _setPointer(500, 1400);            // below the character
  const down = facePosition(settle(c));
  assert.ok(down.y > rest.y + 2, `pointer below: the face went to ${down.y}, rest was ${rest.y}`);

  _setPointer(500, -800);            // above the character
  const up = facePosition(settle(c));
  assert.ok(up.y < rest.y - 2, `pointer above: the face went to ${up.y}, rest was ${rest.y}`);

  c.destroy();
});

test("the turn is bounded by the configured range", async () => {
  const { _setPointer } = await import("../src/follow.js");
  const el = fakeElement({ left: 0, top: 0, width: 100, height: 100 });
  const c = new FollowController(el, { enabled: true, yawRange: 20, pitchRange: 10, blink: false }, null, 2);
  _setPointer(100000, 100000);
  const pose = settle(c, 4);
  assert.ok(Math.abs(pose.yaw) <= (20 * Math.PI) / 180 + 0.02, `yaw ran past its range: ${pose.yaw}`);
  assert.ok(Math.abs(pose.pitch) <= (10 * Math.PI) / 180 + 0.02, `pitch ran past its range: ${pose.pitch}`);
  c.destroy();
});

test("the head goes back to rest when the pointer stops moving", async () => {
  const { _setPointer } = await import("../src/follow.js");
  const el = fakeElement({ left: 400, top: 300, width: 200, height: 200 });
  const c = new FollowController(el, { enabled: true, yawRange: 40, blink: false }, null, 3);

  _setPointer(1400, 400);
  assert.ok(Math.abs(settle(c).yaw) > 0.1, "should have turned first");

  _setPointer(null);                 // pointer left the window
  const rested = settle(c, 3);
  assert.ok(Math.abs(rested.yaw) < 0.01, `did not return to rest: yaw ${rested.yaw}`);
  c.destroy();
});

test("switching follow off returns the character to its authored pose", async () => {
  const { _setPointer } = await import("../src/follow.js");
  const el = fakeElement({ left: 0, top: 0, width: 100, height: 100 });
  const poses = [];
  const c = new FollowController(el, { enabled: true, yawRange: 40, blink: false },
    (p) => poses.push(Object.assign({}, p)), 4);
  _setPointer(900, 900);
  settle(c);
  c.configure({ enabled: false });
  assert.deepEqual(c.pose, { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0, blink: 0 });
  assert.deepEqual(poses[poses.length - 1], { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0, blink: 0 },
    "the host must be told to redraw at rest, not left mid-turn");
  c.destroy();
});

test("a disabled controller costs nothing per frame", () => {
  const el = fakeElement({ left: 0, top: 0, width: 10, height: 10 });
  const c = new FollowController(el, { enabled: false }, () => {
    assert.fail("a disabled controller must not report a pose");
  }, 5);
  assert.equal(c.step(1 / 60, 0), false);
  c.destroy();
});
