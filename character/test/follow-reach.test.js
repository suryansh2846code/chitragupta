/**
 * A character must have somewhere to travel between "resting" and "as far as
 * it turns".
 *
 * This is the bug that made the feature read as broken while every other test
 * passed. The reach was derived purely from the character's own size, so a 34px
 * avatar in a rail hit full deflection 75 pixels away — across a real window
 * the head sat pinned at its limit and flicked between extremes as the pointer
 * crossed the centre line. Nothing in between was ever drawn, so a perfectly
 * correct tracking loop looked like a two-position switch.
 *
 * The assertion is therefore about the *middle* of the range, not the ends:
 * at a natural working distance a small avatar must be partly turned.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { FollowController, _setPointer } from "../src/follow.js";

function controllerAt(rect, seed) {
  const el = { getBoundingClientRect: () => rect, addEventListener() {}, removeEventListener() {} };
  return new FollowController(el, { enabled: true, yawRange: 40, pitchRange: 24, blink: false }, null, seed);
}

function settle(c, seconds = 2) {
  let t = 0;
  for (let i = 0; i < seconds * 60; i++) { t += 1000 / 60; c.step(1 / 60, t); }
  return c.pose.yaw;
}

test("a small avatar is only partly turned at a natural pointer distance", () => {
  // A 34px rail avatar, with the pointer a couple of hundred pixels away —
  // an ordinary place for it to be while somebody reads the page.
  const rail = { left: 40, top: 300, width: 34, height: 34 };
  const c = controllerAt(rail, 11);
  const limit = (40 * Math.PI) / 180;

  _setPointer(rail.left + 17 + 120, rail.top + 17);
  const near = Math.abs(settle(c));
  assert.ok(near > 0.08 * limit, `at 120px the head barely moved (${(near / limit).toFixed(2)} of range)`);
  assert.ok(near < 0.92 * limit, `at 120px the head was already pinned (${(near / limit).toFixed(2)} of range)`);

  c.destroy();
});

test("the deflection grows with distance instead of jumping to the limit", () => {
  const rail = { left: 40, top: 300, width: 34, height: 34 };
  const c = controllerAt(rail, 12);
  const at = (dx) => {
    _setPointer(rail.left + 17 + dx, rail.top + 17);
    return Math.abs(settle(c));
  };
  const a = at(60), b = at(140), d = at(260);
  assert.ok(a < b && b < d, `not monotonic: 60px=${a.toFixed(3)} 140px=${b.toFixed(3)} 260px=${d.toFixed(3)}`);
  // And the steps are real, not floating-point dust.
  assert.ok(b - a > 0.02 && d - b > 0.02, "the gradient between distances is too flat to see");
  c.destroy();
});

test("a large preview still reaches its limit without the pointer leaving the page", () => {
  const preview = { left: 400, top: 200, width: 420, height: 420 };
  const c = controllerAt(preview, 13);
  const limit = (40 * Math.PI) / 180;
  _setPointer(preview.left + 210 + 900, preview.top + 210);
  assert.ok(Math.abs(settle(c)) > 0.9 * limit, "a big preview should be fully turned by 900px away");
  c.destroy();
});
