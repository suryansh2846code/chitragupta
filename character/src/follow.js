/**
 * Look at the cursor.
 *
 * One `pointermove` listener and one `requestAnimationFrame` loop for the whole
 * page, shared by every character on it. That is not micro-optimisation: an
 * sidebar draws eight characters, a member list draws thirty, and a
 * per-instance listener plus a per-instance rAF means thirty callbacks and
 * thirty layout reads per pointer move. The shared loop reads the pointer once
 * and each character does arithmetic against a rect it already has.
 *
 * The motion itself is a damped spring rather than an ease: a spring has no
 * duration, so a pointer that changes direction mid-flight is followed from
 * wherever the head currently is, at whatever speed it currently has. Every
 * tween-based version of this felt like the head was catching up with a
 * decision it had already made.
 *
 * Three behaviours that are easy to leave out and immediately missed:
 *
 *   - **Rest.** After the pointer has been still for a while the head returns
 *     to its authored pose instead of staring at the last known position. A
 *     character frozen mid-glance looks broken, not attentive.
 *   - **Blink.** Staggered per instance. A rail of characters blinking in
 *     unison is unsettling in a way people notice without being able to say why.
 *   - **Stop when nobody is looking.** Hidden tab, off-screen element, or
 *     `prefers-reduced-motion` — the loop stops entirely rather than spinning.
 */

import { clamp, deg, rng } from "./math.js";

const instances = new Set();
let rafId = 0;
let lastTime = 0;
let pointer = null;           // {x, y} in client coordinates, or null before first move
let pointerAt = 0;            // timestamp of the last real pointer movement
let listening = false;

/** How long the pointer must be still before characters drift back to rest. */
const REST_AFTER_MS = 2600;

/**
 * How far away the pointer has to be for a character to turn as far as it can.
 *
 * Scaled off the character's own size, but clamped — and the clamp is the whole
 * point. Reach was once *only* `size * 2.2`, which for a 34px avatar in a rail
 * is 75 pixels: the cursor cleared full deflection almost the instant it left
 * the avatar, so across a 1400px window the head sat pinned at its limit and
 * flicked between extremes as the pointer crossed the centre line. It was
 * tracking perfectly and it read as broken, because nothing in between was ever
 * drawn.
 *
 * The floor gives a small avatar a real gradient to travel across; the ceiling
 * stops a large preview from needing the pointer in the next room.
 */
const REACH_MIN = 210;
const REACH_MAX = 700;

function reachFor(rect) {
  return clamp(Math.max(rect.width, rect.height) * 3.2, REACH_MIN, REACH_MAX);
}

/** Largest frame step the integrator will accept, so a backgrounded tab does not explode the spring. */
const MAX_DT = 1 / 20;

function now() {
  return typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
}

function prefersReducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function onPointerMove(e) {
  pointer = { x: e.clientX, y: e.clientY };
  pointerAt = now();
  start();
}

function onPointerLeave() {
  // Not null — null means "never moved". Leaving the window should send every
  // character back to rest, which the idle path already does.
  pointerAt = 0;
}

function listen() {
  if (listening || typeof window === "undefined") return;
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerdown", onPointerMove, { passive: true });
  document.addEventListener("pointerleave", onPointerLeave, { passive: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) start(); });
  listening = true;
}

function start() {
  if (rafId || typeof requestAnimationFrame !== "function") return;
  if (typeof document !== "undefined" && document.hidden) return;
  lastTime = now();
  rafId = requestAnimationFrame(tick);
}

function stop() {
  if (rafId) cancelAnimationFrame(rafId);
  rafId = 0;
}

function tick(t) {
  rafId = 0;
  const dt = Math.min(MAX_DT, Math.max(0, (t - lastTime) / 1000)) || 1 / 60;
  lastTime = t;

  let alive = false;
  for (const inst of instances) {
    if (inst.step(dt, t)) alive = true;
  }
  // Nothing is still moving and nothing needs to: let the browser idle. The
  // next pointer move or blink schedule starts the loop again.
  if (alive && instances.size) rafId = requestAnimationFrame(tick);
}

/**
 * A single character's follow state.
 *
 * `onPose` is called only on frames where the pose actually changed enough to
 * be worth a re-render — below a tenth of a degree there is nothing on screen
 * to see, and re-projecting several hundred points to produce identical path
 * data is the most expensive way to do nothing.
 */
export class FollowController {
  constructor(element, config, onPose, seed) {
    this.el = element;
    this.onPose = onPose;
    this.rand = rng(seed || Math.floor(Math.random() * 1e9));
    this.pose = { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0, blink: 0 };
    this.vel = { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0 };
    this.rect = null;
    this.rectAt = 0;
    this.blinkAt = 0;
    this.blinkPhase = -1;
    this.enabled = false;
    this.visible = true;
    this.configure(config);
    this._observe();
  }

  configure(config) {
    const c = config || {};
    this.cfg = {
      enabled: c.enabled !== false,
      scope: c.scope === "window" ? "window" : "element",
      yawRange: c.yawRange == null ? 26 : c.yawRange,
      pitchRange: c.pitchRange == null ? 16 : c.pitchRange,
      eyeShift: c.eyeShift == null ? 35 : c.eyeShift,
      stiffness: c.stiffness == null ? 9 : c.stiffness,
      damping: c.damping == null ? 0.85 : c.damping,
      blink: c.blink !== false,
      respectReducedMotion: c.respectReducedMotion !== false,
    };
    const wanted = this.cfg.enabled && !(this.cfg.respectReducedMotion && prefersReducedMotion());
    if (wanted === this.enabled) return;
    this.enabled = wanted;
    if (wanted) {
      listen();
      instances.add(this);
      this.blinkAt = now() + 1200 + this.rand() * 4000;
      start();
    } else {
      instances.delete(this);
      // Leaving a half-turned head on screen would look like the feature
      // crashed. Going back to the authored pose is the documented state.
      this.pose = { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0, blink: 0 };
      this.vel = { yaw: 0, pitch: 0, eyeX: 0, eyeY: 0 };
      if (this.onPose) this.onPose(this.pose);
      if (!instances.size) stop();
    }
  }

  /**
   * An off-screen character is not worth a frame.
   *
   * The rail keeps every agent mounted while only a few are scrolled into view,
   * and IntersectionObserver is the difference between "the loop costs what the
   * visible characters cost" and "the loop costs what every character ever
   * created costs".
   */
  _observe() {
    if (typeof IntersectionObserver !== "function" || !this.el) return;
    this.io = new IntersectionObserver((entries) => {
      for (const e of entries) this.visible = e.isIntersecting;
      if (this.visible) start();
    }, { rootMargin: "64px" });
    this.io.observe(this.el);
  }

  _measure(t) {
    // A rect read is a forced layout. Once every 500ms is enough for a rail
    // that only moves when the page scrolls, and it keeps the pointer handler
    // off the critical path entirely.
    if (this.rect && t - this.rectAt < 500) return this.rect;
    if (!this.el || !this.el.getBoundingClientRect) return null;
    this.rect = this.el.getBoundingClientRect();
    this.rectAt = t;
    return this.rect;
  }

  _target(t) {
    if (!pointer || !this.visible) return { nx: 0, ny: 0 };
    if (pointerAt && now() - pointerAt > REST_AFTER_MS) return { nx: 0, ny: 0 };

    if (this.cfg.scope === "window") {
      const w = window.innerWidth || 1, h = window.innerHeight || 1;
      return { nx: clamp((pointer.x - w / 2) / (w / 2), -1, 1), ny: clamp((pointer.y - h / 2) / (h / 2), -1, 1) };
    }
    const r = this._measure(t);
    if (!r || !r.width) return { nx: 0, ny: 0 };
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const reach = reachFor(r);
    return { nx: clamp((pointer.x - cx) / reach, -1, 1), ny: clamp((pointer.y - cy) / reach, -1, 1) };
  }

  step(dt, t) {
    if (!this.enabled) return false;
    const { nx, ny } = this._target(t);

    // Positive yaw turns the face toward screen-right; positive pitch bows the
    // head FORWARD, so the face points down. Both signs are the rendered
    // behaviour, checked by eye against a pitch sweep — the second one was
    // negated here for a while on the strength of an argument about which way
    // the top of the head moves, which is the opposite of what a viewer sees.
    // `character/test` asserts this against the drawn face, not against the
    // sign of the number, so the mistake cannot be re-derived.
    const targetYaw = nx * deg(this.cfg.yawRange);
    const targetPitch = ny * deg(this.cfg.pitchRange);
    const eyeAmt = this.cfg.eyeShift / 100;
    const targetEyeX = nx * eyeAmt;
    const targetEyeY = ny * eyeAmt;

    const k = this.cfg.stiffness;
    const c = 2 * this.cfg.damping * Math.sqrt(k);
    let moved = 0;
    for (const [key, target] of [["yaw", targetYaw], ["pitch", targetPitch], ["eyeX", targetEyeX], ["eyeY", targetEyeY]]) {
      const x = this.pose[key];
      const v = this.vel[key];
      const a = k * (target - x) - c * v;
      const nv = v + a * dt;
      const nxv = x + nv * dt;
      moved += Math.abs(nxv - x);
      this.vel[key] = nv;
      this.pose[key] = nxv;
    }

    if (this.cfg.blink) moved += this._blink(t);

    // A twentieth of a degree of yaw is the threshold below which re-projecting
    // the character cannot change a single rounded path coordinate.
    if (moved > 0.0009) {
      if (this.onPose) this.onPose(this.pose);
      return true;
    }
    // Still worth a frame if a blink is pending, so the loop does not shut down
    // one second before every character was due to blink.
    return this.cfg.blink && t < this.blinkAt + 400;
  }

  _blink(t) {
    if (this.blinkPhase < 0) {
      if (t < this.blinkAt) return 0;
      this.blinkPhase = 0;
    }
    this.blinkPhase += 1 / 7;           // roughly 120ms closed-and-open at 60fps
    if (this.blinkPhase >= 1) {
      this.blinkPhase = -1;
      this.pose.blink = 0;
      this.blinkAt = t + 2600 + this.rand() * 5200;
      return 1;
    }
    this.pose.blink = Math.sin(this.blinkPhase * Math.PI);
    return 1;
  }

  destroy() {
    instances.delete(this);
    if (this.io) this.io.disconnect();
    this.io = null;
    this.el = null;
    this.onPose = null;
    if (!instances.size) stop();
  }
}

/** For tests and for hosts that drive the pointer themselves. */
export function _setPointer(x, y) {
  pointer = x == null ? null : { x, y };
  pointerAt = now();
}

export function _activeCount() {
  return instances.size;
}
