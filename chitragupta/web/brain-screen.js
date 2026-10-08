/**
 * The full-screen "Your brain" view — a canvas point cloud whose density
 * tracks the memory count.
 *
 * **This is a requestAnimationFrame loop, which CSS cannot reach.** A
 * `prefers-reduced-motion` media query stops every animation on the page except
 * this one, so the loop has to ask as well — `_lessMotion()` in core.js — and
 * draw a single static frame when the answer is yes. A user who asked the
 * system for less motion and got a spinning particle field was not asking
 * about CSS.
 *
 * The loop is started by `openBrainScreen` and must be stopped by
 * `closeBrainScreen`: a rAF chain left running behind a hidden element burns a
 * core for as long as the app is open.
 */

let _bsPoll = null, _bsRaf = null, _bsNodes = null, _bsRot = 0, _bsDensity = 0.15;
let _bsVisible = 520, _bsVisTarget = 520;   // how many nodes light up — grows with entities
// mouse interaction state for the brain viz
let _bsMX = 0, _bsMY = 0, _bsTMX = 0, _bsTMY = 0;       // eased vs target parallax
let _bsHoverX = -9999, _bsHoverY = -9999;               // cursor in canvas px
let _bsDrag = false, _bsDragRot = 0, _bsSpin = 1, _bsLastX = 0, _bsWired = false;
function _bsWire() {
  if (_bsWired) return; _bsWired = true;
  const cv = $("#bsCanvas"); if (!cv) return;
  cv.style.cursor = "grab";
  cv.addEventListener("pointermove", (e) => {
    const r = cv.getBoundingClientRect();
    _bsHoverX = e.clientX - r.left; _bsHoverY = e.clientY - r.top;
    _bsTMX = (_bsHoverX / r.width) * 2 - 1; _bsTMY = (_bsHoverY / r.height) * 2 - 1;
    if (_bsDrag) { _bsDragRot += (e.clientX - _bsLastX) * 0.006; _bsLastX = e.clientX; }
  });
  cv.addEventListener("pointerleave", () => { _bsHoverX = -9999; _bsHoverY = -9999; _bsTMX = 0; _bsTMY = 0; });
  cv.addEventListener("pointerdown", (e) => { _bsDrag = true; _bsLastX = e.clientX; _bsSpin = 0.15; cv.style.cursor = "grabbing"; try { cv.setPointerCapture(e.pointerId); } catch (_) {} });
  const end = () => { _bsDrag = false; _bsSpin = 1; cv.style.cursor = "grab"; };
  cv.addEventListener("pointerup", end); cv.addEventListener("pointercancel", end);
}
function _bsBuildNodes() {
  // a pool of points on a jittered sphere → reads as a neural cluster. We render a
  // growing slice of it (_bsVisible) so the cloud visibly fills in as the knowledge
  // graph grows (more entities → more nodes light up during enrichment).
  const N = 1100, pts = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    const jit = 0.12;
    pts.push({ x: Math.cos(th) * r + (Math.random() - 0.5) * jit, y: y + (Math.random() - 0.5) * jit,
      z: Math.sin(th) * r + (Math.random() - 0.5) * jit, p: Math.random() * 6.28 });
  }
  _bsNodes = pts;
}
function _bsDraw() {
  const cv = $("#bsCanvas"); if (!cv || $("#brainScreen").hidden) return;
  const dpr = Math.min(devicePixelRatio || 1, 2), W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  _bsMX += (_bsTMX - _bsMX) * 0.06; _bsMY += (_bsTMY - _bsMY) * 0.06;   // ease parallax
  _bsRot += 0.0016 * _bsSpin;
  // centre the sphere in the space to the right of the tools panel
  const cx = Math.min(W / 2 + 180, W - 60), cy = H * 0.46, R = Math.min(W * 0.5, H) * 0.34;
  // rotation = auto-spin + drag + mouse parallax; tilt up/down with the cursor
  const ay = _bsRot + _bsDragRot + _bsMX * 0.5, tilt = -_bsMY * 0.4;
  const sy = Math.sin(ay), cyr = Math.cos(ay), st = Math.sin(tilt), ct = Math.cos(tilt), t = Date.now() / 1000;
  const bright = 0.5 + 0.5 * _bsDensity;   // fuller/brighter as the brain grows
  _bsVisible += (_bsVisTarget - _bsVisible) * 0.05;   // ease node count toward target
  const vis = Math.max(60, Math.min(_bsNodes.length, Math.round(_bsVisible)));
  const HOV = 130, proj = [];
  for (let i = 0; i < vis; i++) {   // render a growing slice as the graph fills in
    const n = _bsNodes[i];
    const x1 = n.x * cyr + n.z * sy, z1 = -n.x * sy + n.z * cyr, y1 = n.y;
    const y2 = y1 * ct - z1 * st, z2 = y1 * st + z1 * ct;
    const sx = cx + x1 * R, sYy = cy - y2 * R, depth = (z2 + 1) / 2;
    const hd = Math.hypot(sx - _bsHoverX, sYy - _bsHoverY);
    proj.push({ sx, sy: sYy, depth, p: n.p, near: hd < HOV ? 1 - hd / HOV : 0 });
  }
  // synapse links between nearby points (brighter near the cursor)
  ctx.lineWidth = 0.6;
  for (let i = 0; i < proj.length; i += 1) {
    for (let j = i + 1; j < Math.min(i + 8, proj.length); j++) {
      const dx = proj[i].sx - proj[j].sx, dy = proj[i].sy - proj[j].sy, d = dx * dx + dy * dy;
      const nearBoost = Math.max(proj[i].near, proj[j].near);
      if (d < (52 + nearBoost * 40) * (52 + nearBoost * 40) && (proj[i].depth > 0.3 || nearBoost > 0)) {
        const la = 0.04 + 0.08 * proj[i].depth * bright + nearBoost * 0.35;
        ctx.strokeStyle = `rgba(${170 + nearBoost * 60 | 0},${188 + nearBoost * 45 | 0},${215 + nearBoost * 30 | 0},${la.toFixed(3)})`;
        ctx.beginPath(); ctx.moveTo(proj[i].sx, proj[i].sy); ctx.lineTo(proj[j].sx, proj[j].sy); ctx.stroke();
      }
    }
  }
  // probe lines from the cursor to the nodes it's hovering
  if (_bsHoverX > -9000) {
    for (const p of proj) {
      if (p.near > 0.15) {
        ctx.strokeStyle = `rgba(245,200,119,${(p.near * 0.4).toFixed(3)})`;   // the cursor draws in gold
        ctx.beginPath(); ctx.moveTo(_bsHoverX, _bsHoverY); ctx.lineTo(p.sx, p.sy); ctx.stroke();
      }
    }
  }
  for (const p of proj) {
    const pulse = 0.5 + 0.5 * Math.sin(t * 1.5 + p.p);
    const a = Math.min(1, (0.22 + 0.62 * p.depth) * (0.6 + 0.4 * pulse) * bright + p.near * 0.6);
    const rad = 0.7 + 1.8 * p.depth + p.near * 2.4;
    // --star #dfe7f2 as starlight: cool white, warming slightly toward the cursor
    ctx.fillStyle = `rgba(${196 + 30 * p.depth + p.near * 45 | 0},${208 + 24 * p.depth + p.near * 20 | 0},${228 + 15 * p.depth - p.near * 30 | 0},${a.toFixed(3)})`;
    ctx.beginPath(); ctx.arc(p.sx, p.sy, rad, 0, 6.283); ctx.fill();
  }
  // A rAF loop is out of CSS's reach, so the same query is asked here: when the
  // user wants less motion the field is painted once and left as a still.
  if (!_lessMotion()) _bsRaf = requestAnimationFrame(_bsDraw);
}
async function _bsRefresh() {
  try {
    const [s, st] = await Promise.all([api("/api/sync/status"), api("/api/brain/stats")]);
    const mem = st.total || 0, ent = st.graph?.entities || 0, rel = st.graph?.relations || 0;
    $("#bsMem").textContent = mem.toLocaleString();
    $("#bsEnt").textContent = ent.toLocaleString();
    $("#bsRel").textContent = rel.toLocaleString();
    _bsDensity = Math.min(1, 0.15 + mem / 4000 + ent / 2500);
    if (_lessMotion()) _bsDraw();   // no loop is running — repaint the still
    // node count grows with the knowledge graph so the cloud visibly fills in while
    // enrichment runs (memories are static; entities are what climb).
    _bsVisTarget = Math.round(260 + Math.min(1, ent / 1600) * 840);
    const state = $("#bsState");
    if (s.syncing) { state.textContent = "Building your brain…"; state.classList.remove("done"); }
    else { state.textContent = "Brain ready"; state.classList.add("done"); }
  } catch (_) {}
}
function openBrainScreen() {
  const m = $("#brainScreen"); if (!m) return;
  m.hidden = false;
  if (!_bsNodes) _bsBuildNodes();
  _bsWire();                          // mouse: parallax tilt, hover glow, drag-rotate
  try { loadBrain(); } catch (_) {}   // fill the side panel (stats, entities)
  _bsRefresh(); clearInterval(_bsPoll); _bsPoll = setInterval(_bsRefresh, 2500);
  cancelAnimationFrame(_bsRaf); _bsRaf = requestAnimationFrame(_bsDraw);
}
function closeBrainScreen() {
  $("#brainScreen").hidden = true;
  clearInterval(_bsPoll); _bsPoll = null; cancelAnimationFrame(_bsRaf); _bsRaf = null;
}
$("#bsClose").onclick = closeBrainScreen;
// Clean up = remove junk entities, KEEP all the good (LLM/heuristic) graph work.
//
// It used to be two buttons wearing one coat: holding Option turned the same
// control into `POST /api/brain/rebuild`, which **wipes the whole graph** —
// every entity, every relation, every pass of AI enrichment the user paid a
// model to produce — and re-extracts with the offline extractor. Nothing on
// screen said so except the tail of a `title=` tooltip, and the modifier was
// read off `window.event`, a deprecated global.
//
// That is the one thing a control may never be: something other than what it
// says. The hidden half is gone. `/api/brain/rebuild` still exists and is still
// tested; it simply has no way to be triggered by a stray Option key.
{ const rb = $("#rebuildBtn"); if (rb) rb.onclick = async () => {
  rb.disabled = true;
  try {
    rb.textContent = "Cleaning…";
    const r = await api("/api/brain/prune", { method: "POST" });
    toast(r.removed_entities ? `Removed ${r.removed_entities} junk ${r.removed_entities === 1 ? "entity" : "entities"}` : "Graph is already clean");
    loadBrain(); _bsRefresh();
  } catch (e) { toast(errText(e)); }
  finally { rb.disabled = false; rb.textContent = "Clean up"; }
}; }

// Enrich with AI — loop the LLM enricher (uses the connected model) until the
