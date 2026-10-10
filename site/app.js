/* ═══════════════════════════════════════════════════════════════
   Chitragupta Landing — app.js
   Neural Brain 3D Point-Cloud + Avatars + Model Switcher + Waitlist
   ═══════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  // ═══════════════════════════════════════════════════════════
  // 1. NEURAL BRAIN 3D POINT CLOUD (HERO CANVAS)
  // ═══════════════════════════════════════════════════════════
  var canvas = document.getElementById('hero-canvas');
  var angEl = document.getElementById('hero-ang');
  var nodesEl = document.getElementById('hero-nodes');

  if (canvas && window.CHITRAGUPTA_BRAIN_DATA) {
    try {
      var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      var DATA = window.CHITRAGUPTA_BRAIN_DATA;
      var bin = atob(DATA);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var raw = new Int16Array(bytes.buffer);
      var N = raw.length / 3;

      if (nodesEl) nodesEl.textContent = N.toLocaleString();

      var px = new Float32Array(N), py = new Float32Array(N), pz = new Float32Array(N);
      var nx = new Float32Array(N), ny = new Float32Array(N), nz = new Float32Array(N), side = new Float32Array(N);

      for (var k = 0; k < N; k++) {
        var X0 = raw[k * 3] / 32000, Y0 = raw[k * 3 + 1] / 32000, Z0 = raw[k * 3 + 2] / 32000;
        var x = X0, y = Z0, z = -Y0;
        px[k] = x; py[k] = y; pz[k] = z;
        var len = Math.hypot(x, y, z) || 1;
        nx[k] = x / len; ny[k] = y / len; nz[k] = z / len;
        side[k] = X0;
      }

      var ctx = canvas.getContext('2d');
      var off = document.createElement('canvas');
      var ox = off.getContext('2d');

      var W = 0, H = 0;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var CELL = 6;
      var RAMP = " .:-=+iltJoSMW#%@";
      var SCALE = 1.05, CAMZ = 3.0, CY = 0.44;
      var Ld = [0.32, 0.44, 0.84];
      var lMag = Math.hypot(Ld[0], Ld[1], Ld[2]);
      Ld = [Ld[0] / lMag, Ld[1] / lMag, Ld[2] / lMag];

      var rotY = 1.2566, rotX = -0.14, spin = 0.0035, userCtl = true;
      var mX = 0, mY = 0, mtX = 0, mtY = 0, curAY = rotY, curTilt = rotX;
      var hoverX = -9999, hoverY = -9999, HOVR = 140, HOVF = 70;
      var ripple = { on: false, t: 0, ox: 0, oy: 0, oz: 0 }, glitch = 0;

      // Flowing particles
      var FLOWN = 140, flow = [];
      function seedParticle(f, fresh) {
        var idx = (Math.random() * N) | 0;
        var d = Math.hypot(px[idx], py[idx], pz[idx]) || 1;
        f.x = px[idx]; f.y = py[idx]; f.z = pz[idx];
        f.vx = (px[idx] / d) * 0.005 + (Math.random() - 0.5) * 0.002;
        f.vy = (py[idx] / d) * 0.005 + 0.002 + (Math.random() - 0.5) * 0.002;
        f.vz = (pz[idx] / d) * 0.005 + (Math.random() - 0.5) * 0.002;
        f.s = side[idx];
        f.max = 60 + Math.random() * 80;
        f.life = fresh ? Math.random() * f.max : 0;
      }
      for (var fIdx = 0; fIdx < FLOWN; fIdx++) {
        var fp = {};
        seedParticle(fp, true);
        flow.push(fp);
      }

      function resize() {
        var rect = canvas.parentElement.getBoundingClientRect();
        W = rect.width;
        H = rect.height;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        off.width = W * dpr;
        off.height = H * dpr;
        ox.setTransform(dpr, 0, 0, dpr, 0, 0);
        CELL = W < 768 ? 5 : 6;
      }

      function smooth(a, b, val) {
        var t = (val - a) / (b - a);
        if (t < 0) t = 0;
        if (t > 1) t = 1;
        return t * t * (3 - 2 * t);
      }

      var drag = { on: false, x: 0, y: 0, moved: 0 };

      function renderFrame() {
        var cols = Math.ceil(W / CELL), rows = Math.ceil(H / CELL), count = cols * rows;
        var zb = new Float32Array(count);
        for (var q = 0; q < count; q++) zb[q] = -1e9;
        var pi = new Int32Array(count);
        pi.fill(-1);
        var bst = new Float32Array(count);

        var ay = drag.on ? rotY : rotY + mX * 0.28;
        var tilt = drag.on ? rotX : rotX + mY * 0.20;
        curAY = ay; curTilt = tilt;

        var sy = Math.sin(ay), cy = Math.cos(ay);
        var st = Math.sin(tilt), ct = Math.cos(tilt);
        var isWide = W > 900;
        var brainCX = isWide ? W * 0.64 : W * 0.5;
        var brainCY = isWide ? H * 0.46 : H * 0.42;
        var sc = Math.min(W, H) * (isWide ? 0.95 : 0.85);
        var rad = ripple.on ? ripple.t * 1.9 : -1, rw = 0.24;

        for (var pt = 0; pt < N; pt++) {
          var X = px[pt], Y = py[pt], Z = pz[pt];
          var x1 = X * cy + Z * sy, z1 = -X * sy + Z * cy, y1 = Y;
          var y2 = y1 * ct - z1 * st, z2 = y1 * st + z1 * ct;

          var Nx = nx[pt], Ny = ny[pt], Nz = nz[pt];
          var nx1 = Nx * cy + Nz * sy, nz1 = -Nx * sy + Nz * cy, ny1 = Ny;
          var ny2 = ny1 * ct - nz1 * st, nz2 = ny1 * st + nz1 * ct;

          if (nz2 < -0.34) continue;
          var zc = CAMZ - z2;
          if (zc < 0.2) continue;

          var scx = (x1 * sc) / zc + brainCX;
          var scy = (-y2 * sc) / zc + brainCY;
          var col = (scx / CELL) | 0, row = (scy / CELL) | 0;

          if (col < 0 || col >= cols || row < 0 || row >= rows) continue;
          var cellId = row * cols + col;

          if (z2 > zb[cellId]) {
            zb[cellId] = z2;
            pi[cellId] = pt;
            if (rad > 0) {
              var d = Math.sqrt((X - ripple.ox) * (X - ripple.ox) + (Y - ripple.oy) * (Y - ripple.oy) + (Z - ripple.oz) * (Z - ripple.oz));
              bst[cellId] = Math.exp(-((d - rad) * (d - rad)) / (2 * rw * rw));
            }
          }
        }

        ox.setTransform(dpr, 0, 0, dpr, 0, 0);
        ox.clearRect(0, 0, W, H);

        // Constellation grid dots around periphery
        var gsp = 36, ccx = brainCX, ccy = brainCY, edgeR = Math.min(W, H) * 0.32;
        for (var gy = gsp; gy < H; gy += gsp) {
          for (var gx = gsp; gx < W; gx += gsp) {
            var ex = gx - ccx, ey = gy - ccy, ed = Math.sqrt(ex * ex + ey * ey);
            var edge = ed < edgeR ? 0 : Math.min(1, (ed - edgeR) / (edgeR * 0.6));
            if (edge <= 0) continue;
            var hxd = gx - hoverX, hyd = gy - hoverY, hd = Math.sqrt(hxd * hxd + hyd * hyd);
            var near = hd < 180 ? 1 - hd / 180 : 0;
            var ga = (0.06 + near * 0.24) * edge;
            if (ga < 0.012) continue;
            ox.fillStyle = 'rgba(223, 231, 242,' + ga.toFixed(3) + ')';
            var gr = 1.2 + near * 1.5;
            ox.fillRect(gx - gr / 2, gy - gr / 2, gr, gr);
          }
        }

        // Draw ASCII neural brain characters
        ox.font = (CELL + 1) + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
        ox.textBaseline = 'top';

        for (var r = 0; r < rows; r++) {
          for (var c = 0; c < cols; c++) {
            var id2 = r * cols + c, k2 = pi[id2];
            if (k2 < 0) continue;

            var nX = nx[k2], nY = ny[k2], nZ = nz[k2];
            var rnx = nX * cy + nZ * sy, rnz = -nX * sy + nZ * cy, rny = nY;
            var rny2 = rny * ct - rnz * st, rnz2 = rny * st + rnz * ct;
            var L = rnx * Ld[0] + rny2 * Ld[1] + rnz2 * Ld[2];
            if (L < 0) L = 0;

            var b3 = bst[id2];
            var lum = Math.min(1, 0.18 + 0.82 * L + b3 * 0.9);
            var ci = (lum * (RAMP.length - 1)) | 0;
            if (ci < 1) ci = 1;
            if (ci > RAMP.length - 1) ci = RAMP.length - 1;

            var gg = 38 + 206 * lum;
            var mR = gg * 0.90, mG = gg * 0.95, mB = gg;

            // Warm gold pole-star accenting on hover
            var dd = Math.hypot(c * CELL - hoverX, r * CELL - hoverY);
            var rv = 1 - smooth(HOVR - HOVF, HOVR, dd);
            var toGold = rv * 0.7 + b3 * 0.8;
            if (toGold > 1) toGold = 1;

            var R = mR + (245 - mR) * toGold;
            var G = mG + (200 - mG) * toGold;
            var B = mB + (119 - mB) * toGold;

            ox.fillStyle = 'rgba(' + (R | 0) + ',' + (G | 0) + ',' + (B | 0) + ',' + (0.35 + 0.65 * lum).toFixed(3) + ')';
            ox.fillText(RAMP[ci], c * CELL, r * CELL);
          }
        }

        // Flowing particles
        for (var p = 0; p < flow.length; p++) {
          var item = flow[p];
          item.x += item.vx; item.y += item.vy; item.z += item.vz;
          item.life++;
          if (item.life > item.max) seedParticle(item, false);

          var pX = item.x, pY = item.y, pZ = item.z;
          var px1 = pX * cy + pZ * sy, pz1 = -pX * sy + pZ * cy, py1 = pY;
          var py2 = py1 * ct - pz1 * st, pz2 = py1 * st + pz1 * ct;
          var pzc = CAMZ - pz2;
          if (pzc < 0.2) continue;

          var pscx = (px1 * sc) / pzc + brainCX;
          var pscy = (-py2 * sc) / pzc + brainCY;
          var alpha = (1 - item.life / item.max) * 0.45;

          ox.fillStyle = 'rgba(223, 231, 242,' + alpha.toFixed(3) + ')';
          ox.fillRect(pscx, pscy, 1.5, 1.5);
        }

        // Blit to screen
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(off, 0, 0);

        // Glitch on pulse
        var dx = (glitch * 7 + (ripple.on ? ripple.t * 4 : 0)) * dpr;
        if (dx > 0.6) {
          ctx.globalCompositeOperation = 'lighter';
          ctx.globalAlpha = 0.25;
          ctx.drawImage(off, -dx, 0);
          ctx.drawImage(off, dx, 0);
          ctx.globalCompositeOperation = 'source-over';
          ctx.globalAlpha = 1;
        }
      }

      var lastTime = 0;
      function animLoop(timestamp) {
        if (timestamp - lastTime > 30) {
          mX += (mtX - mX) * 0.05;
          mY += (mtY - mY) * 0.05;

          if (!drag.on && !userCtl) rotY += spin;

          if (ripple.on) {
            ripple.t += 0.045;
            if (ripple.t > 2.2) ripple.on = false;
          }

          glitch *= 0.86;
          if (Math.random() < 0.003) glitch = Math.random() * 0.35;

          renderFrame();

          if (angEl) {
            var degVal = ((Math.round(rotY * 57.29) % 360) + 360) % 360;
            angEl.textContent = String(degVal).padStart(3, '0');
          }
          lastTime = timestamp;
        }
        requestAnimationFrame(animLoop);
      }

      function projPoint(ptIdx, cy, sy, ct, st, sc) {
        var X = px[ptIdx], Y = py[ptIdx], Z = pz[ptIdx];
        var x1 = X * cy + Z * sy, z1 = -X * sy + Z * cy, y1 = Y;
        var y2 = y1 * ct - z1 * st, z2 = y1 * st + z1 * ct;
        var zc = CAMZ - z2;
        var isWide = W > 900;
        var bCX = isWide ? W * 0.64 : W * 0.5;
        var bCY = isWide ? H * 0.46 : H * 0.42;
        return [(x1 * sc) / zc + bCX, (-y2 * sc) / zc + bCY, z2, zc];
      }

      function fireSignal(cx, cy2) {
        var r = canvas.getBoundingClientRect();
        var mx = cx - r.left, my = cy2 - r.top;
        var sy = Math.sin(curAY), cy = Math.cos(curAY);
        var st = Math.sin(curTilt), ct = Math.cos(curTilt);
        var sc = Math.min(W, H) * SCALE;
        var best = -1, bd = 1e9;
        for (var p = 0; p < N; p += 2) {
          var pt = projPoint(p, cy, sy, ct, st, sc);
          if (pt[3] < 0.2) continue;
          var distSq = (pt[0] - mx) * (pt[0] - mx) + (pt[1] - my) * (pt[1] - my);
          if (distSq < bd) {
            bd = distSq;
            best = p;
          }
        }
        if (best >= 0) {
          ripple.on = true;
          ripple.t = 0;
          ripple.ox = px[best];
          ripple.oy = py[best];
          ripple.oz = pz[best];
          glitch = 0.55;
        }
      }

      canvas.addEventListener('pointerdown', function (e) {
        drag.on = true;
        drag.x = e.clientX;
        drag.y = e.clientY;
        drag.moved = 0;
        canvas.setPointerCapture(e.pointerId);
      });

      canvas.addEventListener('pointermove', function (e) {
        if (!drag.on) return;
        var dxp = e.clientX - drag.x, dyp = e.clientY - drag.y;
        drag.moved += Math.abs(dxp) + Math.abs(dyp);
        rotY += dxp * 0.005;
        rotX += dyp * 0.005;
        userCtl = true;
        drag.x = e.clientX;
        drag.y = e.clientY;
      });

      canvas.addEventListener('pointerup', function (e) {
        if (drag.on && drag.moved < 5) fireSignal(e.clientX, e.clientY);
        drag.on = false;
      });

      canvas.addEventListener('dblclick', function () {
        userCtl = !userCtl;
      });

      window.addEventListener('mousemove', function (e) {
        var r = canvas.getBoundingClientRect();
        var hx = e.clientX - r.left, hy = e.clientY - r.top;
        mtX = (hx / W) * 2 - 1;
        mtY = (hy / H) * 2 - 1;
        hoverX = hx;
        hoverY = hy;
      });

      canvas.addEventListener('mouseleave', function () {
        hoverX = -9999;
        hoverY = -9999;
      });

      // Ambient periodic signal pulse
      setInterval(function () {
        if (!ripple.on && Math.random() < 0.45) {
          var k = (Math.random() * N) | 0;
          ripple.on = true;
          ripple.t = 0;
          ripple.ox = px[k];
          ripple.oy = py[k];
          ripple.oz = pz[k];
        }
      }, 4500);

      window.addEventListener('resize', resize);
      resize();

      if (reduce) {
        renderFrame();
      } else {
        requestAnimationFrame(animLoop);
      }
    } catch (err) {
      console.warn('Neural brain renderer initialization skipped:', err);
    }
  }

  // ═══════════════════════════════════════════════════════════
  // 2. LIVE AGENT AVATARS (CHARACTER.JS)
  // ═══════════════════════════════════════════════════════════
  // The seed lives on the element, so adding an agent to the page is a markup
  // change rather than an edit to a roster kept down here.
  function initAgentAvatars() {
    if (typeof window.Character === 'undefined' || !window.Character.generateScene) return;

    var slots = document.querySelectorAll('[data-avatar-seed]');
    Array.prototype.forEach.call(slots, function (slot) {
      var seed = slot.getAttribute('data-avatar-seed');
      var name = slot.getAttribute('aria-label') || seed;
      var size = parseInt(slot.getAttribute('data-avatar-size'), 10) || 40;
      try {
        var scene = window.Character.generateScene(seed);
        if (!scene || !scene.scene) return;
        scene.scene.camera.frame = 'none';
        scene.scene.effects.showAvatarShadow = false;
        scene.scene.camera.padding = 3;
        slot.innerHTML = window.Character.renderToString(scene, { size: size, title: name });
      } catch (e) {
        console.warn('Avatar generation skipped for ' + seed, e);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAgentAvatars);
  } else {
    initAgentAvatars();
  }

  // ═══════════════════════════════════════════════════════════
  // 3. INTERACTIVE MODEL SWITCHER
  // ═══════════════════════════════════════════════════════
  var modelTabs = document.querySelectorAll('.picker-tab');
  var modelNameEl = document.getElementById('model-name');
  var modelDescEl = document.getElementById('model-desc');

  var modelDetails = {
    claude: {
      name: 'Anthropic Claude 3.7 Sonnet',
      desc: '"Optimal nuanced reasoning and adaptive style. Memory context is injected directly from your local disk on every turn."'
    },
    gpt4o: {
      name: 'OpenAI GPT-4o (Omni)',
      desc: '"High throughput and tool dispatch. Stored memory references remain identical across provider transitions."'
    },
    ollama: {
      name: 'Ollama — Local Llama 3.3 (100% Offline)',
      desc: '"Runs locally on Apple silicon with zero network transmission. Private, fast, and completely immune to cloud outages."'
    },
    deepseek: {
      name: 'DeepSeek R1 Reasoning Engine',
      desc: '"Deep verifiable chain-of-thought analysis. Works directly with your local source graph."'
    }
  };

  modelTabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      modelTabs.forEach(function (t) {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
      });
      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');

      var key = tab.getAttribute('data-model');
      if (modelDetails[key]) {
        if (modelNameEl) modelNameEl.textContent = modelDetails[key].name;
        if (modelDescEl) modelDescEl.textContent = modelDetails[key].desc;
      }
    });
  });

  // ═══════════════════════════════════════════════════════════
  // 4. AIRPLANE MODE TOGGLE
  // ═══════════════════════════════════════════════════════
  var airplaneStatus = document.getElementById('airplane-status');
  if (airplaneStatus) {
    airplaneStatus.style.cursor = 'pointer';
    airplaneStatus.addEventListener('click', function () {
      if (airplaneStatus.textContent.indexOf('DISCONNECTED') !== -1) {
        airplaneStatus.textContent = '● WI-FI CONNECTED';
        airplaneStatus.style.color = '#8ea8e0';
        airplaneStatus.style.borderColor = 'rgba(142, 168, 224, 0.4)';
      } else {
        airplaneStatus.textContent = '● WI-FI DISCONNECTED';
        airplaneStatus.style.color = 'var(--ok)';
        airplaneStatus.style.borderColor = 'rgba(95, 207, 142, 0.3)';
      }
    });
  }

  // ═══════════════════════════════════════════════════════════
  // 5. STICKY NAV SCROLL
  // ═══════════════════════════════════════════════════════
  var nav = document.getElementById('nav');
  var lastScrollY = window.pageYOffset || 0;

  window.addEventListener('scroll', function () {
    var currentY = window.pageYOffset || 0;
    if (nav) {
      if (currentY > 120 && currentY > lastScrollY && currentY - lastScrollY > 8) {
        nav.style.transform = 'translateY(-100%)';
      } else {
        nav.style.transform = 'translateY(0)';
      }
    }
    lastScrollY = currentY;
  }, { passive: true });

  // ═══════════════════════════════════════════════════════════
  // 6. WAITLIST SUBMISSION (D1 API + LOCAL DEV FALLBACK)
  // ═══════════════════════════════════════════════════════
  var form = document.getElementById('waitlist-form');
  var submitBtn = document.getElementById('wl-submit');
  var successBox = document.getElementById('waitlist-success');
  var errorBox = document.getElementById('waitlist-error');
  var errorMsg = document.getElementById('error-msg');
  var posEl = document.getElementById('success-position');

  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();

      // Check honeypot
      var hp = form.elements['website'];
      if (hp && hp.value) return;

      var email = (form.elements['email'].value || '').trim();
      var name = (form.elements['name'].value || '').trim();
      var linkedin = (form.elements['linkedin'] ? form.elements['linkedin'].value : '').trim();
      var useCase = (form.elements['use_case'] ? form.elements['use_case'].value : '').trim();

      if (!email || !name) {
        showError('Please provide both your name and a valid email address.');
        return;
      }

      setLoading(true);

      var payload = {
        name: name,
        email: email,
        linkedin: linkedin || null,
        use_case: useCase || null
      };

      fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      .then(function (res) {
        if (!res.ok) {
          // If 404 or 405 (static server like npx serve), fallback gracefully to simulated local queue
          if (res.status === 404 || res.status === 405) {
            return simulateLocalWaitlist(payload);
          }
          return res.json().then(function (errData) {
            throw new Error(errData.error || 'Server responded with error');
          });
        }
        return res.json();
      })
      .then(function (data) {
        setLoading(false);
        showSuccess(data.position || 412, data.already_registered);
      })
      .catch(function (err) {
        // Fallback gracefully for local dev preview
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
          var mockData = simulateLocalWaitlist(payload);
          setLoading(false);
          showSuccess(mockData.position, false);
        } else {
          setLoading(false);
          showError(err.message || 'Something went wrong. Please try again.');
        }
      });
    });
  }

  function simulateLocalWaitlist(payload) {
    var stored = localStorage.getItem('chitragupta_waitlist_signups');
    var list = stored ? JSON.parse(stored) : [];
    var existing = list.find(function (item) { return item.email.toLowerCase() === payload.email.toLowerCase(); });
    if (existing) {
      return { ok: true, position: existing.position, already_registered: true };
    }
    var assignedPos = 400 + list.length + 1;
    payload.position = assignedPos;
    payload.timestamp = Date.now();
    list.push(payload);
    localStorage.setItem('chitragupta_waitlist_signups', JSON.stringify(list));
    return { ok: true, position: assignedPos, already_registered: false };
  }

  function setLoading(loading) {
    if (!submitBtn) return;
    submitBtn.disabled = loading;
    if (loading) {
      submitBtn.classList.add('loading');
    } else {
      submitBtn.classList.remove('loading');
    }
  }

  function showSuccess(pos, already) {
    if (form) form.hidden = true;
    if (errorBox) errorBox.hidden = true;
    if (successBox) {
      successBox.hidden = false;
      if (posEl) posEl.textContent = '#' + pos;
      var note = successBox.querySelector('.success-note');
      if (note && already) {
        note.textContent = 'You were already registered on the waitlist! We will send you one email when the beta opens.';
      }
      successBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  function showError(msg) {
    if (errorBox) {
      errorBox.hidden = false;
      if (errorMsg) errorMsg.textContent = msg;
    }
  }

  // ═══════════════════════════════════════════════════════════
  // 6. COMPETITOR COMPARISON TABS (MUSE, DOTS, GROK BOTS)
  // ═══════════════════════════════════════════════════════════
  var btnCompAgents = document.getElementById('btn-compare-agents');
  var btnCompChatbots = document.getElementById('btn-compare-chatbots');
  var tableWrapAgents = document.getElementById('table-wrap-agents');
  var tableWrapChatbots = document.getElementById('table-wrap-chatbots');

  if (btnCompAgents && btnCompChatbots && tableWrapAgents && tableWrapChatbots) {
    btnCompAgents.addEventListener('click', function () {
      btnCompAgents.classList.add('active');
      btnCompChatbots.classList.remove('active');
      tableWrapAgents.classList.remove('is-hidden');
      tableWrapChatbots.classList.add('is-hidden');
    });

    btnCompChatbots.addEventListener('click', function () {
      btnCompChatbots.classList.add('active');
      btnCompAgents.classList.remove('active');
      tableWrapChatbots.classList.remove('is-hidden');
      tableWrapAgents.classList.add('is-hidden');
    });
  }

  // ═══════════════════════════════════════════════════════════
  // 7. DESKTOP APP WORKSPACE INTERACTIVE DISPLAY
  // ═══════════════════════════════════════════════════════════
  function initWorkspaceDisplay() {
    var workspaceSection = document.getElementById('workspace');
    if (!workspaceSection) return;

    // Authentic Chitragupta 3D geometric SVG avatar engine
    var SAVED_AVATAR_DOCS = {
      "chotu": {"schema":"character.scene","version":1,"metadata":{"name":"chotu"},"scene":{"appearance":{"paletteId":"ember","backgroundStyle":"solid","background":null},"camera":{"size":256,"frame":"rounded","fit":"contain","padding":10,"showFrameShadow":true,"frameShadow":{"color":"#000000","direction":90,"distance":12,"opacity":22,"softness":24}},"entity":{"preset":"cat","parts":[{"id":"ear-l","role":"body","shape":"cone","color":null,"shade":0,"positionX":-34,"positionY":46,"positionZ":-34,"width":38,"height":44,"depth":28,"rotationX":0,"rotationY":0,"rotationZ":12,"round":30,"taper":55,"faceHost":false,"outline":true},{"id":"ear-r","role":"body","shape":"cone","color":null,"shade":0,"positionX":34,"positionY":46,"positionZ":-34,"width":38,"height":44,"depth":28,"rotationX":0,"rotationY":0,"rotationZ":-12,"round":30,"taper":55,"faceHost":false,"outline":true},{"id":"body","role":"body","shape":"rounded-box","color":null,"shade":0,"positionX":0,"positionY":0,"positionZ":0,"width":104,"height":96,"depth":92,"rotationX":0,"rotationY":0,"rotationZ":0,"round":84,"taper":55,"faceHost":true,"outline":true}]},"face":{"enabled":true,"color":null,"offsetX":0,"offsetY":0,"rotation":0,"width":18,"height":42,"gap":40,"eyeShape":"ellipse","eyeRoundness":97,"leftEyeRotation":0,"rightEyeRotation":0,"eyeHighlight":{"enabled":false,"color":"#ffffff","offsetX":-18,"offsetY":-20,"opacity":92,"size":24},"mouthEnabled":false,"mouthShape":"curve","mouthWidth":50,"mouthHeight":14,"mouthY":50,"mouthCurve":70,"mouthRotation":0,"noseEnabled":true,"noseShape":"inverted-triangle","noseWidth":54,"noseHeight":7,"noseY":34,"noseRotation":0},"effects":{"showOutline":true,"outline":{"color":null,"opacity":80,"width":4},"showAvatarShadow":true,"avatarShadow":{"color":"#000000","direction":45,"distance":12,"opacity":24,"softness":16},"showFaceShadow":false,"faceShadow":{"color":"#000000","direction":50,"distance":4,"opacity":28,"softness":0},"seams":true,"colorGrade":{"brightness":1,"saturation":1,"tintAmount":0,"tintR":0,"tintG":0,"tintB":0}},"lighting":{"enabled":false,"azimuth":-35,"elevation":40,"strength":40},"follow":{"enabled":true,"scope":"element","yawRange":26,"pitchRange":16,"eyeShift":35,"stiffness":9,"damping":0.85,"blink":true,"respectReducedMotion":true},"view":{"yaw":-0.05282031250000008,"pitch":-0.15161718750000003,"roll":-0.03,"scale":1.07,"positionX":0,"positionY":0},"decals":[]}},
      "fashion-manager-and-stylist": {"schema":"character.scene","version":1,"metadata":{"name":"fashion-manager-and-stylist"},"scene":{"appearance":{"paletteId":"moss","backgroundStyle":"solid","background":null},"camera":{"size":256,"frame":"rounded","fit":"contain","padding":10,"showFrameShadow":true,"frameShadow":{"color":"#000000","direction":90,"distance":12,"opacity":22,"softness":24}},"entity":{"preset":"custom","parts":[{"id":"ellipsoid","role":"body","shape":"wedge","color":null,"shade":0,"positionX":20,"positionY":0,"positionZ":-10,"width":200,"height":200,"depth":92,"rotationX":0,"rotationY":0,"rotationZ":0,"round":84,"taper":55,"faceHost":true,"outline":false},{"id":"wedge","role":"body","shape":"wedge","color":null,"shade":16,"positionX":120,"positionY":0,"positionZ":0,"width":200,"height":200,"depth":92,"rotationX":0,"rotationY":0,"rotationZ":0,"round":0,"taper":100,"faceHost":false,"outline":false}]},"face":{"enabled":true,"color":null,"offsetX":0,"offsetY":0,"rotation":0,"width":20,"height":41,"gap":38,"eyeShape":"ellipse","eyeRoundness":55,"leftEyeRotation":-7,"rightEyeRotation":7,"eyeHighlight":{"enabled":false,"color":"#ffffff","offsetX":-18,"offsetY":-20,"opacity":92,"size":24},"mouthEnabled":false,"mouthShape":"curve","mouthWidth":52,"mouthHeight":12,"mouthY":52,"mouthCurve":45,"mouthRotation":0,"noseEnabled":true,"noseShape":"oval","noseWidth":9,"noseHeight":7,"noseY":34,"noseRotation":0},"effects":{"showOutline":true,"outline":{"color":null,"opacity":80,"width":10.5},"showAvatarShadow":true,"avatarShadow":{"color":"#000000","direction":45,"distance":12,"opacity":24,"softness":16},"showFaceShadow":false,"faceShadow":{"color":"#000000","direction":50,"distance":4,"opacity":28,"softness":0},"seams":true,"colorGrade":{"brightness":1,"saturation":1,"tintAmount":0,"tintR":0,"tintG":0,"tintB":0}},"lighting":{"enabled":false,"azimuth":-35,"elevation":40,"strength":40},"follow":{"enabled":true,"scope":"element","yawRange":26,"pitchRange":16,"eyeShift":35,"stiffness":9,"damping":0.85,"blink":true,"respectReducedMotion":true},"view":{"yaw":-0.11151562499999998,"pitch":0.047742187500000005,"roll":0.01,"scale":1.07,"positionX":0,"positionY":0},"decals":[]}}
    };

    function getAgentScene(agentId) {
      var key = agentId;
      if (key === 'fashion-stylist') key = 'fashion-manager-and-stylist';
      if (SAVED_AVATAR_DOCS[key]) {
        return JSON.parse(JSON.stringify(SAVED_AVATAR_DOCS[key]));
      }
      if (typeof Character !== 'undefined' && Character.generateScene) {
        return Character.generateScene(key);
      }
      return null;
    }

    function renderAgentAvatarSvg(agentId, size) {
      size = size || 32;
      var doc = getAgentScene(agentId);
      if (doc && typeof Character !== 'undefined' && Character.renderToString) {
        doc.scene.camera.frame = 'none';
        doc.scene.effects.showAvatarShadow = false;
        doc.scene.camera.padding = 4;
        return Character.renderToString(doc, { size: size, title: agentId });
      }
      return '<svg viewBox="0 0 40 40" width="' + size + '" height="' + size + '"><circle cx="20" cy="20" r="18" fill="#242220"/></svg>';
    }

    function paintAgentAvatarOrb(el, agentId, size, live) {
      if (!el) return;
      var doc = getAgentScene(agentId);
      if (doc && typeof Character !== 'undefined') {
        doc.scene.camera.frame = 'none';
        doc.scene.effects.showAvatarShadow = false;
        doc.scene.camera.padding = 4;
        if (live && typeof Character.createCharacter === 'function') {
          if (el._character && typeof el._character.destroy === 'function') {
            el._character.destroy();
            el._character = null;
          }
          el.innerHTML = '';
          el._character = Character.createCharacter(el, doc, { title: agentId });
          return;
        }
        if (el._character && typeof el._character.destroy === 'function') {
          el._character.destroy();
          el._character = null;
        }
        el.innerHTML = Character.renderToString(doc, { size: size || 32, title: agentId });
        return;
      }
      el.innerHTML = renderAgentAvatarSvg(agentId, size);
    }

    // Authentic agents from Chitragupta app (matching the user's desktop screenshot)
    var AGENTS_DATA = {
      'personal-manager': {
        id: 'personal-manager',
        name: 'personal manager',
        role: 'on my daily life and things',
        sidebarRole: 'on my daily l...',
        provider: 'Anthropic',
        suggestions: [
          'Cap ping interval to work hours (9 AM - 6 PM)',
          'Authorize WhatsApp browser connector for Tushar',
          'Show all active launchd automations'
        ],
        getInitialChat: function () {
          return [
            {
              type: 'agent',
              sender: 'personal manager',
              text: 'No duplicate exists. I\'ll set up a repeating automation that fires every 2 minutes and sends you a notification saying "hi".\n\nNote: the minimum interval this system\'s schedule trigger reliably supports is what <code class="app-code-chip">interval_min</code> you approve on the card — I put the instruction for "every 2 minutes" in the routine text itself, but if the card only lets you set the number in whole minutes, set it to <code class="app-code-chip">2</code> there before confirming (I can\'t force that value past the card UI). Also worth flagging: this will ping you nonstop until you pause it — want me to also cap it (e.g., only during work hours) or is constant fine?',
              cards: [
                {
                  type: 'automation',
                  id: 'card-auto-user-1',
                  head: 'Create automation',
                  badges: ['AUTOMATION', 'DONE'],
                  name: 'Hi every 2 minutes',
                  runs: 'Every hour · chief-of-staff',
                  body: 'Every 2 minutes, send Suryansh a desktop notification with the message "hi". No other action needed.',
                  result: "✓ Automation 'Hi every 2 minutes' created — runs every hour, as chief-of-staff",
                  undone: false
                }
              ]
            },
            {
              type: 'action-result',
              text: '[Action result] New automation “Hi every 2 minutes” — every hour — confirmed. Automation \'Hi every 2 minutes\' created — runs every hour, as chief-of-staff'
            },
            {
              type: 'user',
              text: 'message tushar bhaiya hi in whatsapp'
            },
            {
              type: 'agent',
              sender: 'personal manager',
              text: 'I can\'t send this yet — WhatsApp works through your browser connector, but I don\'t currently have permission to use the Browser connector to dispatch outbound messages without your explicit confirmation. Would you like me to open the WhatsApp Web connector in your sandboxed browser to send this to Tushar?'
            }
          ];
        }
      },

      'chief-of-staff': {
        id: 'chief-of-staff',
        name: 'Chief of Staff',
        role: 'your day, end to end',
        sidebarRole: 'your day, en...',
        provider: 'Anthropic',
        suggestions: [
          'Propose a morning routine for 8:30 AM',
          'Who is Priya and what did we discuss?',
          'What are my unresolved open loops?'
        ],
        getInitialChat: function () {
          return [
            {
              type: 'user',
              text: "Give me my morning brief: what's on today, what needs a reply, and what commitments are open?"
            },
            {
              type: 'agent',
              sender: 'Chief of Staff',
              text: "Here is your morning brief: You have an Architecture Sync at 11:00 AM with the team and a 3:00 PM catch-up. Sarah at Sequoia followed up on our Q3 offline agent benchmarks. Your open loop on finalizing the slide 14 offline latency report is still open. I drafted your reply and scheduled a reminder before your sync:",
              cards: [
                {
                  type: 'email',
                  id: 'card-chief-email-1',
                  head: 'Save a draft',
                  badges: ['EMAIL', 'NEEDS YOUR CONFIRMATION'],
                  to: 'sarah@sequoia.com',
                  subject: 'Re: Chitragupta Q3 Benchmarks & Offline Agent Architecture',
                  body: "Hi Sarah,\n\nFollowing up on our conversation: Chitragupta runs 100% locally on Apple Silicon with 2.8ms sqlite-vec recall and zero cloud data leaks. Attached the slide 14 summary for our chat.\n\nBest,\nSuryansh",
                  approved: false
                },
                {
                  type: 'reminder',
                  id: 'card-chief-remind-1',
                  head: 'Set reminder',
                  badges: ['REMINDER', 'NEEDS YOUR CONFIRMATION'],
                  title: 'Finalize Slide 14 offline latency benchmarks',
                  time: 'Today, 10:30 AM (30m before Architecture Sync)',
                  completed: false
                }
              ]
            }
          ];
        }
      },

      'chotu': {
        id: 'chotu',
        name: 'chotu',
        role: 'lead agent - orchestrates sub-agents',
        sidebarRole: 'lead agent - ...',
        provider: 'Anthropic',
        suggestions: [
          'Pause all non-critical background syncs',
          'Review sub-agent execution health',
          'Run memory compaction on sqlite-vec'
        ],
        getInitialChat: function () {
          return [
            {
              type: 'user',
              text: 'Status check across active sub-agents and pending background jobs.'
            },
            {
              type: 'agent',
              sender: 'chotu',
              text: 'All 5 local sub-agents are healthy on Apple Silicon. Chief of Staff queued 2 morning reminders; personal manager registered 1 recurring notification trigger; local sqlite-vec indexed 3,950 memories. I prepared a delegated sync check for your confirmation:',
              cards: [
                {
                  type: 'automation',
                  id: 'card-chotu-auto-1',
                  head: 'Review sub-agent tasks',
                  badges: ['ORCHESTRATION', 'ACTIVE'],
                  name: 'Sub-agent health heartbeat',
                  runs: 'Every 15 minutes · chotu',
                  body: 'Inspect daemon sockets for personal manager, chief-of-staff, and chef. Auto-restart on unhandled panic.',
                  result: '✓ 5 sub-agents active · Apple Silicon Metal GPU ready',
                  undone: false
                }
              ]
            }
          ];
        }
      },

      'chef': {
        id: 'chef',
        name: 'CHEF',
        role: 'help me with recipes and nutrition',
        sidebarRole: 'help me with ...',
        provider: 'Anthropic',
        suggestions: [
          'Set 20-minute oven bake timer',
          'Generate grocery list for tomorrow',
          'Show nutritional macros breakdown'
        ],
        getInitialChat: function () {
          return [
            {
              type: 'user',
              text: 'What can I cook tonight with the groceries we bought on Tuesday? Keep it high protein.'
            },
            {
              type: 'agent',
              sender: 'CHEF',
              text: "Recalled Tuesday's grocery receipt from your local store: you have chicken breast, baby spinach, Greek yogurt, feta, and cold-pressed olive oil. I designed a 25-minute Mediterranean Lemon Herb Chicken (48g protein) and set prep steps:",
              cards: [
                {
                  type: 'automation',
                  id: 'card-chef-plan-1',
                  head: 'Meal plan & timer',
                  badges: ['NUTRITION', 'DONE'],
                  name: 'Mediterranean Lemon Herb Chicken (48g Protein)',
                  runs: 'Dinner · 25 minutes prep',
                  body: '1. Season chicken breast with lemon, garlic, and dried oregano.\n2. Sear 6m per side on medium-high skillet.\n3. Toss baby spinach with Greek yogurt & crumbled feta dressing.',
                  result: '✓ Recipe indexed into health log · timer ready',
                  undone: false
                }
              ]
            }
          ];
        }
      },

      'social-media-manager': {
        id: 'social-media-manager',
        name: 'social media manager',
        role: 'My social media channels and voice',
        sidebarRole: 'My social me...',
        provider: 'Anthropic',
        suggestions: [
          'Shorten into a 3-tweet thread',
          'Add benchmark charts from slide 14',
          'Draft LinkedIn announcement version'
        ],
        getInitialChat: function () {
          return [
            {
              type: 'user',
              text: "Draft a post about Chitragupta's local offline benchmarks for X/Twitter."
            },
            {
              type: 'agent',
              sender: 'social media manager',
              text: "Drafted a launch announcement matching your past high-engagement technical posts. Emphasizes 2.8ms local sqlite-vec latency and 0 bytes cloud leakage:",
              cards: [
                {
                  type: 'email',
                  id: 'card-social-post-1',
                  head: 'Draft social post',
                  badges: ['SOCIAL', 'NEEDS YOUR CONFIRMATION'],
                  to: 'X / Twitter (@suryansh)',
                  subject: 'Post Draft: Why your AI shouldn\'t start from zero',
                  body: "Every AI assistant starts from zero every time you open it.\n\nWe built Chitragupta: an open, private brain for your Mac that remembers what you did, reads your local files, and runs automations 100% offline.\n\n2.8ms vector recall. Apple Silicon native. Zero telemetry.",
                  approved: false
                }
              ]
            }
          ];
        }
      },

      'fashion-stylist': {
        id: 'fashion-stylist',
        name: 'fashion manager and stylist',
        role: 'manage my fits and wardrobe',
        sidebarRole: 'manage my f...',
        provider: 'Anthropic',
        suggestions: [
          'Alternative option with blazer',
          'Add rain-resistant layer',
          'Save this outfit combination to favorites'
        ],
        getInitialChat: function () {
          return [
            {
              type: 'user',
              text: "What should I wear to tomorrow's investor dinner in SF? Forecast is 58°F and breezy."
            },
            {
              type: 'agent',
              sender: 'fashion manager and stylist',
              text: "Checked local weather forecast and your indexed wardrobe catalog. Recommend a tailored charcoal overshirt over an off-white merino knit, dark selvedge denim, and minimalist leather boots. Polished smart casual, perfect for 58°F breeze:",
              cards: [
                {
                  type: 'automation',
                  id: 'card-fashion-outfit-1',
                  head: 'Outfit proposal',
                  badges: ['WARDROBE', 'DONE'],
                  name: 'Smart Casual · SF Dinner (58°F)',
                  runs: 'Tomorrow evening · private recommendation',
                  body: 'Top: Charcoal wool-blend overshirt + off-white merino crewneck\nBottom: Dark indigo raw selvedge denim\nFootwear: Black Chelsea boots (water-resistant)',
                  result: '✓ Outfit logged to style rotation',
                  undone: false
                }
              ]
            }
          ];
        }
      }
    };

    // Keep active conversation histories in memory - default to personal-manager to match screenshot!
    var activeAgentId = 'personal-manager';
    var conversationHistories = {};
    Object.keys(AGENTS_DATA).forEach(function (id) {
      conversationHistories[id] = AGENTS_DATA[id].getInitialChat();
    });

    // DOM Elements
    var agentListEl = document.getElementById('appAgentList');
    var messagesLogEl = document.getElementById('appMessagesLog');
    var activeOrbEl = document.getElementById('activeAgentOrb');
    var activeNameEl = document.getElementById('activeAgentName');
    var activeRoleEl = document.getElementById('activeAgentRole');
    var activeProviderLabelEl = document.getElementById('activeProviderLabel');
    var cmpModelPillTextEl = document.getElementById('cmpModelPillText');
    var windowAgentTitleEl = document.getElementById('window-agent-title');
    var resetBtn = document.getElementById('appResetBtn');
    var appInputEl = document.getElementById('appInput');

    // Populate sidebar agent avatar orbs
    var orbChief = document.getElementById('orb-chief-of-staff');
    var orbChotu = document.getElementById('orb-chotu');
    var orbPersonal = document.getElementById('orb-personal-manager');
    var orbChef = document.getElementById('orb-chef');
    var orbSocial = document.getElementById('orb-social-media-manager');
    var orbFashion = document.getElementById('orb-fashion-stylist');

    if (orbChief) paintAgentAvatarOrb(orbChief, 'chief-of-staff', 32, true);
    if (orbChotu) paintAgentAvatarOrb(orbChotu, 'chotu', 32, true);
    if (orbPersonal) paintAgentAvatarOrb(orbPersonal, 'personal-manager', 32, true);
    if (orbChef) paintAgentAvatarOrb(orbChef, 'chef', 32, true);
    if (orbSocial) paintAgentAvatarOrb(orbSocial, 'social-media-manager', 32, true);
    if (orbFashion) paintAgentAvatarOrb(orbFashion, 'fashion-stylist', 32, true);

    function showToast(msg) {
      var existing = document.querySelector('.app-toast');
      if (existing) existing.remove();

      var toast = document.createElement('div');
      toast.className = 'app-toast';
      toast.innerHTML = '<span class="app-toast-dot"></span><span>' + msg + '</span>';
      document.body.appendChild(toast);
      setTimeout(function () {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(function () { toast.remove(); }, 300);
      }, 3500);
    }

    // Switch active agent
    function switchAgent(agentId) {
      if (!AGENTS_DATA[agentId]) return;
      activeAgentId = agentId;
      var data = AGENTS_DATA[agentId];

      // Update sidebar buttons
      if (agentListEl) {
        var items = agentListEl.querySelectorAll('.app-agent-item');
        items.forEach(function (btn) {
          if (btn.getAttribute('data-agent') === agentId) {
            btn.classList.add('active');
          } else {
            btn.classList.remove('active');
          }
        });
      }

      // Update header bar & window title
      if (windowAgentTitleEl) windowAgentTitleEl.textContent = data.name;
      if (activeOrbEl) paintAgentAvatarOrb(activeOrbEl, agentId, 32, true);
      if (activeNameEl) activeNameEl.textContent = data.name;
      if (activeRoleEl) activeRoleEl.textContent = data.role;
      if (activeProviderLabelEl) activeProviderLabelEl.textContent = data.provider;
      if (cmpModelPillTextEl) cmpModelPillTextEl.textContent = data.provider;
      if (appInputEl) appInputEl.placeholder = 'Message ' + data.name + '...';

      renderMessages();
    }

    // Render message stream (Frameless AI messages directly on canvas)
    function renderMessages() {
      if (!messagesLogEl) return;
      messagesLogEl.innerHTML = '';
      var history = conversationHistories[activeAgentId] || [];

      history.forEach(function (msg, idx) {
        if (msg.type === 'user') {
          var userDiv = document.createElement('div');
          userDiv.className = 'app-msg-user';
          userDiv.textContent = msg.text;
          messagesLogEl.appendChild(userDiv);
        } else if (msg.type === 'action-result') {
          var resDiv = document.createElement('div');
          resDiv.className = 'app-action-result-line';
          resDiv.textContent = msg.text;
          messagesLogEl.appendChild(resDiv);
        } else {
          var botDiv = document.createElement('div');
          botDiv.className = 'app-msg-bot';

          var bubbleDiv = document.createElement('div');
          bubbleDiv.className = 'app-msg-bubble';

          // Text content (supports newlines and inline code chips)
          if (msg.text) {
            var paragraphs = msg.text.split('\n\n');
            paragraphs.forEach(function (para) {
              var p = document.createElement('p');
              p.innerHTML = para.replace(/\n/g, '<br>');
              bubbleDiv.appendChild(p);
            });
          }

          // Action cards
          if (msg.cards && msg.cards.length) {
            msg.cards.forEach(function (card) {
              var cardEl = renderCard(card, idx);
              if (cardEl) bubbleDiv.appendChild(cardEl);
            });
          }

          botDiv.appendChild(bubbleDiv);
          messagesLogEl.appendChild(botDiv);
        }
      });

      messagesLogEl.scrollTop = messagesLogEl.scrollHeight;
    }

    // Render action card - Authentic App Mac-Style Card
    function renderCard(card, msgIndex) {
      var cardDiv = document.createElement('div');
      cardDiv.className = 'action-card';
      cardDiv.id = card.id;

      if (card.type === 'automation') {
        var isUndone = card.undone;
        if (!isUndone) cardDiv.dataset.settled = 'done';
        cardDiv.dataset.kind = 'Automation';
        cardDiv.dataset.risk = 'green';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">' + (card.head || 'Create automation') + '</div>',
          '<div class="ac-badges-row">',
          '  <span class="ac-badge-pill">' + (card.badge || 'AUTOMATION') + '</span>',
          '  <span class="ac-badge-pill ' + (isUndone ? 'undone' : 'done') + '">' + (isUndone ? 'CANCELLED' : (card.statusBadge || 'DONE')) + '</span>',
          '</div>',
          '<div class="ac-row"><span class="ac-row-label">Name</span> <span class="ac-row-val">' + card.name + '</span></div>',
          '<div class="ac-row"><span class="ac-row-label">Runs</span> <span class="ac-row-val">' + card.runs + '</span></div>',
          '<div class="ac-body">' + card.body + '</div>',
          '<div class="ac-result-line ' + (isUndone ? 'undone' : '') + '">',
          '  <span class="ac-check-icon">' + (isUndone ? '✕' : '✓') + '</span> ' + (isUndone ? 'Automation removed from schedule daemon' : (card.resultText || card.result || ("Automation '" + card.name + "' created — runs every hour, as chief-of-staff"))),
          '</div>',
          '<div class="ac-actions">',
          '  <button type="button" class="ac-btn-undo" data-action="undo-auto">' + (isUndone ? 'Restore' : 'Undo') + '</button>',
          '</div>'
        ].join('');

        var undoBtn = cardDiv.querySelector('[data-action="undo-auto"]');
        if (undoBtn) {
          undoBtn.addEventListener('click', function () {
            card.undone = !card.undone;
            renderMessages();
            showToast(card.undone ? 'Automation undone. Unscheduled from launchd daemon.' : 'Automation re-enabled in launchd daemon.');
          });
        }
      } else if (card.type === 'email') {
        var isApproved = card.approved;
        if (isApproved) cardDiv.dataset.settled = 'done';
        cardDiv.dataset.kind = 'Email';
        cardDiv.dataset.risk = 'green';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Save a draft</div>',
          '<span class="ac-kind">Email</span>',
          '<span class="ac-tag">' + (isApproved ? 'done' : 'needs your confirmation') + '</span>',
          '<div class="ac-row"><b>To</b> ' + card.to + '</div>',
          '<div class="ac-row"><b>Subject</b> ' + card.subject + '</div>',
          '<div class="ac-body"><textarea class="ac-field-wide" id="ta-' + card.id + '" ' + (isApproved ? 'disabled' : '') + '>' + card.body + '</textarea></div>',
          '<div class="ac-row muted ac-risk">🔒 ' + card.risk + '</div>',
          '<div class="ac-actions">',
          (isApproved
            ? '<button type="button" class="ac-confirm" disabled style="background:#28c941;color:#121110;">✓ Saved to drafts</button>'
            : '<button type="button" class="ac-confirm" data-action="approve-email">Confirm &amp; save</button>' +
              '<button type="button" class="ac-cancel" data-action="dismiss-email">Cancel</button>'
          ),
          '</div>',
          '<div class="ac-result">' + (isApproved ? '<span class="ac-ok">✓ Draft saved to Apple Mail · confirmed just now</span>' : '') + '</div>'
        ].join('');

        var approveBtn = cardDiv.querySelector('[data-action="approve-email"]');
        if (approveBtn) {
          approveBtn.addEventListener('click', function () {
            card.approved = true;
            renderMessages();
            showToast('Consent confirmed. In the real Mac app, this dispatches via Apple Mail; zero data left your browser.');
          });
        }
        var dismissBtn = cardDiv.querySelector('[data-action="dismiss-email"]');
        if (dismissBtn) {
          dismissBtn.addEventListener('click', function () {
            cardDiv.dataset.settled = 'cancelled';
            cardDiv.style.opacity = '0.45';
            showToast('Draft cancelled.');
          });
        }
      } else if (card.type === 'reminder') {
        var isDone = card.completed;
        if (isDone) cardDiv.dataset.settled = 'done';
        cardDiv.dataset.kind = 'Reminder';
        cardDiv.dataset.risk = 'green';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Set reminder</div>',
          '<span class="ac-kind">Reminder</span>',
          '<span class="ac-tag">' + (isDone ? 'done' : 'needs your confirmation') + '</span>',
          '<div class="ac-row"><b>Remind</b> ' + card.title + '</div>',
          '<div class="ac-row"><b>When</b> ' + card.time + '</div>',
          '<div class="ac-row muted ac-risk">🔒 ' + card.risk + '</div>',
          '<div class="ac-actions">',
          (isDone
            ? '<button type="button" class="ac-cancel" data-action="toggle-remind">Mark Incomplete</button>'
            : '<button type="button" class="ac-confirm" data-action="toggle-remind">Confirm &amp; set</button>' +
              '<button type="button" class="ac-cancel" data-action="snooze-remind">Snooze 1h</button>'
          ),
          '</div>',
          '<div class="ac-result">' + (isDone ? '<span class="ac-ok">✓ Scheduled in Apple Reminders · confirmed just now</span>' : '') + '</div>'
        ].join('');

        var toggleRemindBtn = cardDiv.querySelector('[data-action="toggle-remind"]');
        if (toggleRemindBtn) {
          toggleRemindBtn.addEventListener('click', function () {
            card.completed = !card.completed;
            renderMessages();
            showToast(card.completed ? 'Reminder scheduled in Apple Reminders.' : 'Reminder restored.');
          });
        }
        var snoozeBtn = cardDiv.querySelector('[data-action="snooze-remind"]');
        if (snoozeBtn) {
          snoozeBtn.addEventListener('click', function () {
            showToast('Snoozed for 1 hour.');
          });
        }
      } else if (card.type === 'triage') {
        var isConfirmed = card.confirmed;
        if (isConfirmed) cardDiv.dataset.settled = 'done';
        cardDiv.dataset.kind = 'Inbox';
        cardDiv.dataset.risk = 'amber';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Change your inbox</div>',
          '<span class="ac-kind">Inbox</span>',
          '<span class="ac-tag">' + (isConfirmed ? 'done' : 'needs your confirmation') + '</span>',
          '<div class="ac-row"><b>Summary</b> ' + card.summary + '</div>',
          '<div class="ac-row"><b>Included</b> ' + card.detail + '</div>',
          '<div class="ac-body">Archive 14 emails · ' + card.detail + '</div>',
          '<div class="ac-row muted ac-risk">⚠️ ' + card.risk + '</div>',
          '<div class="ac-actions">',
          (isConfirmed
            ? '<button type="button" class="ac-confirm" disabled style="background:#28c941;color:#121110;">✓ 14 Archived</button>'
            : '<button type="button" class="ac-confirm" data-action="confirm-triage">Confirm &amp; archive (14)</button>' +
              '<button type="button" class="ac-cancel" data-action="dismiss-triage">Cancel</button>'
          ),
          '</div>',
          '<div class="ac-result">' + (isConfirmed ? '<span class="ac-ok">✓ 14 emails archived on Gmail connector cache</span>' : '') + '</div>'
        ].join('');

        var confTriageBtn = cardDiv.querySelector('[data-action="confirm-triage"]');
        if (confTriageBtn) {
          confTriageBtn.addEventListener('click', function () {
            card.confirmed = true;
            renderMessages();
            showToast('Batch triage executed locally on Gmail connector cache.');
          });
        }
        var disTriageBtn = cardDiv.querySelector('[data-action="dismiss-triage"]');
        if (disTriageBtn) {
          disTriageBtn.addEventListener('click', function () {
            cardDiv.dataset.settled = 'cancelled';
            cardDiv.style.opacity = '0.45';
            showToast('Triage proposal cancelled.');
          });
        }
      } else if (card.type === 'browser') {
        var isExpanded = card.stepsVisible;
        cardDiv.dataset.kind = 'Browser';
        cardDiv.dataset.risk = 'green';
        cardDiv.dataset.settled = 'done';

        var tableRows = card.table.map(function (row) {
          return '<tr><td><strong>' + row.airline + '</strong></td><td>' + row.time + '</td><td class="table-fare">' + row.fare + '</td><td><span style="font-size:10px;padding:2px 5px;background:rgba(245,200,119,0.1);border-radius:3px;color:var(--north);">' + row.note + '</span></td></tr>';
        }).join('');

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Headless browser session</div>',
          '<span class="ac-kind">Browser</span>',
          '<span class="ac-tag">done</span>',
          '<div class="ac-row"><b>URL</b> ' + card.url + '</div>',
          '<div class="ac-row"><b>Status</b> ' + card.statusText + ' (Sandbox PID #4819)</div>',
          '<div class="ac-body" style="padding:0;overflow:hidden;">',
          '<table class="card-table">',
          '  <thead><tr><th>Flight</th><th>Duration</th><th>Fare</th><th>Rating</th></tr></thead>',
          '  <tbody>' + tableRows + '</tbody>',
          '</table>',
          '</div>',
          '<div class="card-browser-steps" id="steps-' + card.id + '" style="' + (isExpanded ? '' : 'display:none;') + '">',
          card.steps.map(function (s) { return '<div class="step-line">' + s + '</div>'; }).join(''),
          '</div>',
          '<div class="ac-row muted ac-risk">🔒 ' + card.risk + '</div>',
          '<div class="ac-actions">',
          '  <button type="button" class="ac-confirm" data-action="toggle-steps">' + (isExpanded ? '▼ Hide Step Replay' : '▶ View Step Replay') + '</button>',
          '  <button type="button" class="ac-cancel" data-action="save-graph">Save to Knowledge Graph</button>',
          '</div>',
          '<div class="ac-result"><span class="ac-ok">✓ 4 steps completed in local Chromium sandbox</span></div>'
        ].join('');

        var stepToggleBtn = cardDiv.querySelector('[data-action="toggle-steps"]');
        if (stepToggleBtn) {
          stepToggleBtn.addEventListener('click', function () {
            card.stepsVisible = !card.stepsVisible;
            renderMessages();
          });
        }
        var saveGraphBtn = cardDiv.querySelector('[data-action="save-graph"]');
        if (saveGraphBtn) {
          saveGraphBtn.addEventListener('click', function () {
            showToast('Flight fare matrix indexed into local sqlite-vec database.');
          });
        }
      } else if (card.type === 'cron') {
        var isActive = card.active;
        if (isActive) cardDiv.dataset.settled = 'done';
        cardDiv.dataset.kind = 'Automation';
        cardDiv.dataset.risk = 'green';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Create automation</div>',
          '<span class="ac-kind">Automation</span>',
          '<span class="ac-tag">' + (isActive ? 'active' : 'paused') + '</span>',
          '<div class="ac-row"><b>Runs</b> ' + card.schedule + '</div>',
          '<div class="ac-row"><b>Task</b> ' + card.task + '</div>',
          '<div class="switch-container">',
          '  <label class="toggle-switch">',
          '    <input type="checkbox" ' + (isActive ? 'checked' : '') + ' data-action="toggle-cron">',
          '    <span class="toggle-slider"></span>',
          '  </label>',
          '  <span class="switch-label">' + (isActive ? 'Scheduled for tomorrow 07:30 AM in launchd daemon' : 'Automation paused') + '</span>',
          '</div>',
          '<div class="ac-row muted ac-risk">🔒 ' + card.risk + '</div>',
          '<div class="ac-actions">',
          '  <button type="button" class="ac-cancel" data-action="toggle-btn">' + (isActive ? 'Pause Routine' : 'Resume Routine') + '</button>',
          '</div>',
          '<div class="ac-result">' + (isActive ? '<span class="ac-ok">✓ Routine registered with launchd daemon</span>' : '') + '</div>'
        ].join('');

        var cronCheckbox = cardDiv.querySelector('[data-action="toggle-cron"]');
        if (cronCheckbox) {
          cronCheckbox.addEventListener('change', function () {
            card.active = cronCheckbox.checked;
            renderMessages();
            showToast(card.active ? 'Automation scheduled in local launchd daemon.' : 'Automation paused.');
          });
        }
        var toggleBtn = cardDiv.querySelector('[data-action="toggle-btn"]');
        if (toggleBtn) {
          toggleBtn.addEventListener('click', function () {
            card.active = !card.active;
            renderMessages();
            showToast(card.active ? 'Automation resumed.' : 'Automation paused.');
          });
        }
      } else if (card.type === 'terminal') {
        cardDiv.dataset.kind = 'Command';
        cardDiv.dataset.risk = 'green';
        cardDiv.dataset.settled = 'done';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Run sandbox command</div>',
          '<span class="ac-kind">Command</span>',
          '<span class="ac-tag">done</span>',
          '<div class="ac-row"><b>Command</b> ' + card.command + '</div>',
          '<div class="ac-row"><b>Sandbox</b> macOS sandbox-exec · Exit 0 (' + card.time + ')</div>',
          '<div class="ac-body" style="color:#a8a29e;">',
          '<div style="color:var(--north);font-weight:600;">$ ' + card.command + '</div>',
          '<div style="margin-top:6px;">' + card.output + '</div>',
          '</div>',
          '<div class="ac-row muted ac-risk">🔒 ' + card.risk + '</div>',
          '<div class="ac-actions">',
          '  <button type="button" class="ac-confirm" data-action="rerun-term">Re-run in Sandbox</button>',
          '</div>',
          '<div class="ac-result"><span class="ac-ok">✓ Exit 0 · all vector benchmarks passed</span></div>'
        ].join('');

        var rerunBtn = cardDiv.querySelector('[data-action="rerun-term"]');
        if (rerunBtn) {
          rerunBtn.addEventListener('click', function () {
            rerunBtn.textContent = 'Executing in sandbox...';
            rerunBtn.disabled = true;
            setTimeout(function () {
              rerunBtn.textContent = '✓ Passed (4.2ms)';
              rerunBtn.disabled = false;
              showToast('Executed in macOS isolated sandbox. Exit 0.');
            }, 600);
          });
        }
      } else if (card.type === 'git') {
        var isDrafted = card.drafted;
        if (isDrafted) cardDiv.dataset.settled = 'done';
        cardDiv.dataset.kind = 'Task';
        cardDiv.dataset.risk = 'green';

        cardDiv.innerHTML = [
          '<div class="ac-chrome" aria-hidden="true">',
          '  <span class="ac-dot red"></span><span class="ac-dot yellow"></span><span class="ac-dot green"></span>',
          '</div>',
          '<div class="ac-head">Draft pull request</div>',
          '<span class="ac-kind">Task</span>',
          '<span class="ac-tag">' + (isDrafted ? 'done' : 'needs your confirmation') + '</span>',
          '<div class="ac-row"><b>Branch</b> ' + card.branch + '</div>',
          '<div class="ac-row"><b>Title</b> ' + card.title + '</div>',
          '<div class="ac-row"><b>Changes</b> ' + card.stats + '</div>',
          '<div class="ac-row muted ac-risk">🔒 ' + card.risk + '</div>',
          '<div class="ac-actions">',
          (isDrafted
            ? '<button type="button" class="ac-confirm" disabled style="background:#28c941;color:#121110;">✓ PR #42 Created</button>'
            : '<button type="button" class="ac-confirm" data-action="draft-pr">Confirm &amp; create PR</button>' +
              '<button type="button" class="ac-cancel" data-action="view-diff">View Diff</button>'
          ),
          '</div>',
          '<div class="ac-result">' + (isDrafted ? '<span class="ac-ok">✓ PR #42 created on local repo remote</span>' : '') + '</div>'
        ].join('');

        var draftPrBtn = cardDiv.querySelector('[data-action="draft-pr"]');
        if (draftPrBtn) {
          draftPrBtn.addEventListener('click', function () {
            card.drafted = true;
            renderMessages();
            showToast('Git Pull Request #42 drafted against local git branch.');
          });
        }
        var viewDiffBtn = cardDiv.querySelector('[data-action="view-diff"]');
        if (viewDiffBtn) {
          viewDiffBtn.addEventListener('click', function () {
            showToast('Diff: 3 files changed (+128 -34 lines in engine/recall.rs)');
          });
        }
      }

      return cardDiv;
    }



    // Handle user prompt click from chips
    function handlePromptClick(promptText) {
      if (!promptText || !promptText.trim()) return;
      var clean = promptText.trim();

      // Append user msg
      var history = conversationHistories[activeAgentId];
      history.push({
        type: 'user',
        text: clean
      });
      renderMessages();

      // Typing indicator
      var typingEl = document.createElement('div');
      typingEl.className = 'app-msg-typing';
      typingEl.id = 'workspace-typing-indicator';
      typingEl.innerHTML = '<span class="app-typing-dot"></span><span class="app-typing-dot"></span><span class="app-typing-dot"></span>';
      messagesLogEl.appendChild(typingEl);
      messagesLogEl.scrollTop = messagesLogEl.scrollHeight;

      // Realistic delayed reply
      setTimeout(function () {
        var indicator = document.getElementById('workspace-typing-indicator');
        if (indicator) indicator.remove();

        var reply = generateAgentReply(activeAgentId, clean);
        history.push(reply);
        renderMessages();
      }, 650);
    }

    // Generate grounded replies based on prompt
    function generateAgentReply(agentId, prompt) {
      var lower = prompt.toLowerCase();

      if (agentId === 'personal-manager') {
        if (lower.indexOf('cap') !== -1 || lower.indexOf('work hours') !== -1 || lower.indexOf('interval') !== -1) {
          return {
            type: 'agent',
            sender: 'personal manager',
            text: 'I updated the automation parameters in your local launchd daemon. The notification will now only trigger during work hours (Monday through Friday, 9:00 AM – 6:00 PM IST) and pauses automatically outside that window.',
            cards: [
              {
                type: 'automation',
                id: 'card-auto-pm-workhours-' + Date.now(),
                head: 'Update automation',
                badge: 'AUTOMATION',
                statusBadge: 'DONE',
                name: 'Hi every 2 minutes (Work hours only)',
                runs: 'Every 2m · Mon-Fri 9AM-6PM · personal-manager',
                body: 'Send Suryansh desktop notification "hi" every 2 minutes during active working hours.',
                resultText: "✓ Automation schedule updated — capped to Mon-Fri 9:00 AM – 6:00 PM",
                undone: false
              }
            ]
          };
        } else if (lower.indexOf('whatsapp') !== -1 || lower.indexOf('authorize') !== -1 || lower.indexOf('tushar') !== -1) {
          return {
            type: 'agent',
            sender: 'personal manager',
            text: 'WhatsApp connector permission granted. In Chitragupta, browser automation runs through an isolated local Chromium sandbox (`PID #5120`). Session tokens and cookies stay encrypted in macOS Keychain and are never sent to external servers.',
            cards: [
              {
                type: 'automation',
                id: 'card-auto-pm-wa-' + Date.now(),
                head: 'Browser Connector Session',
                badge: 'BROWSER SANDBOX',
                statusBadge: 'DONE',
                name: 'WhatsApp Web Sandbox connector',
                runs: 'Local Chromium sandbox · on-device session',
                body: 'Dispatched outbound message to Tushar via local headless browser automation.',
                resultText: '✓ WhatsApp dispatch completed — 0 network requests left your machine',
                undone: false
              }
            ]
          };
        } else {
          return {
            type: 'agent',
            sender: 'personal manager',
            text: 'Active local automations retrieved from launchd:\n1. <strong>Hi every 2 minutes</strong> (runs hourly, chief-of-staff)\n2. <strong>Morning Brief</strong> (8:30 AM weekdays)\n3. <strong>Local Memory Compaction</strong> (02:00 AM nightly)\n\nAll automations execute locally with zero cloud dependencies.',
            cards: [
              {
                type: 'automation',
                id: 'card-auto-pm-list-' + Date.now(),
                head: 'Automation daemon report',
                badge: 'LAUNCHD DAEMON',
                statusBadge: 'DONE',
                name: 'System routine inventory',
                runs: '3 active local routines',
                body: 'launchctl list | grep chitragupta -> 3 daemons registered, all status 0 (healthy).',
                resultText: '✓ 3 local background daemons active and monitored',
                undone: false
              }
            ]
          };
        }
      } else if (agentId === 'chief-of-staff') {
        if (lower.indexOf('routine') !== -1 || lower.indexOf('8:30') !== -1) {
          return {
            type: 'agent',
            sender: 'Chief of Staff',
            text: 'I scheduled a daily morning routine for 8:30 AM. Every weekday, I will review your schedule, summarize overnight emails that require replies, and surface your open loops:',
            cards: [
              {
                type: 'automation',
                id: 'card-cron-' + Date.now(),
                head: 'Create automation',
                badge: 'AUTOMATION',
                statusBadge: 'DONE',
                name: 'Daily Executive Brief',
                runs: 'Weekdays at 08:30 AM · chief-of-staff',
                body: 'Review calendar agenda, unread urgent mail, and open commitments at 8:30 AM each weekday morning.',
                resultText: '✓ Routine registered with launchd daemon — next run tomorrow at 8:30 AM',
                undone: false
              }
            ]
          };
        } else if (lower.indexOf('priya') !== -1) {
          return {
            type: 'agent',
            sender: 'Chief of Staff',
            text: 'Priya Sharma is Lead Product Designer on Project Aurora. In your last conversation on Tuesday, you discussed dark mode tokens and agreed to finalize the button contrast ratio.',
            cards: [
              {
                type: 'reminder',
                id: 'card-remind-' + Date.now(),
                head: 'Set reminder',
                badges: ['REMINDER', 'NEEDS YOUR CONFIRMATION'],
                risk: 'Reaches nobody — nothing leaves your machine.',
                title: "Follow up with Priya on Aurora tokens",
                time: 'Today at 4:30 PM',
                completed: false
              }
            ]
          };
        } else {
          return {
            type: 'agent',
            sender: 'Chief of Staff',
            text: 'Found 2 open commitments in local storage: 1) Slide 14 offline benchmark numbers owed to Sarah; 2) Reviewing Aurora tokens with Priya. I indexed both in your tracking queue.',
            cards: [
              {
                type: 'reminder',
                id: 'card-remind-' + Date.now(),
                head: 'Set reminder',
                badges: ['REMINDER', 'NEEDS YOUR CONFIRMATION'],
                risk: 'Reaches nobody — nothing leaves your machine.',
                title: 'Review Slide 14 & Aurora tokens',
                time: 'Tomorrow, 09:30 AM',
                completed: false
              }
            ]
          };
        }
      } else if (agentId === 'chotu') {
        if (lower.indexOf('pause') !== -1) {
          return {
            type: 'agent',
            sender: 'chotu',
            text: 'Suspended non-critical background sync tasks. Foreground agents remain responsive with zero memory overhead on Apple Silicon.',
            cards: [
              {
                type: 'automation',
                id: 'card-chotu-pause-' + Date.now(),
                head: 'Sync policy updated',
                badge: 'ORCHESTRATION',
                statusBadge: 'DONE',
                name: 'Background polling pause',
                runs: 'Immediate',
                body: 'Suspended mail and calendar polling loops until next interaction.',
                resultText: '✓ Background daemons throttled to 0% idle CPU',
                undone: false
              }
            ]
          };
        } else if (lower.indexOf('compaction') !== -1) {
          return {
            type: 'agent',
            sender: 'chotu',
            text: 'Ran local memory compaction in `~/Library/Chitragupta/brain.sqlite`. 3,950 memory chunks and 3,455 entities defragmented; sqlite-vec vector query latency dropped to 1.8ms.',
            cards: [
              {
                type: 'automation',
                id: 'card-chotu-compact-' + Date.now(),
                head: 'Vector compaction',
                badge: 'DATABASE',
                statusBadge: 'DONE',
                name: 'sqlite-vec VACUUM & Re-index',
                runs: 'Immediate · local SQLite',
                body: 'Compacted embeddings table: 3,950 vectors indexed across HNSW graph.',
                resultText: '✓ sqlite-vec compacted · 1.8ms recall latency',
                undone: false
              }
            ]
          };
        } else {
          return {
            type: 'agent',
            sender: 'chotu',
            text: 'Sub-agent health report: All 5 registered agents are nominal and running locally on Apple Silicon Unified Memory. No cloud fallbacks required.',
            cards: [
              {
                type: 'automation',
                id: 'card-chotu-health-' + Date.now(),
                head: 'Sub-agent heartbeat',
                badge: 'ORCHESTRATION',
                statusBadge: 'DONE',
                name: 'Process monitor',
                runs: 'Continuous · on-device',
                body: 'Personal manager (ok), Chief of Staff (ok), Chef (ok), Social media (ok), Stylist (ok).',
                resultText: '✓ All 5 local agents healthy and responsive',
                undone: false
              }
            ]
          };
        }
      } else if (agentId === 'chef') {
        if (lower.indexOf('timer') !== -1) {
          return {
            type: 'agent',
            sender: 'CHEF',
            text: 'Set a 20-minute countdown timer on macOS for the chicken breast bake. I will sound a local notification alert when it is ready.',
            cards: [
              {
                type: 'automation',
                id: 'card-chef-timer-' + Date.now(),
                head: 'Local countdown timer',
                badge: 'TIMER',
                statusBadge: 'DONE',
                name: 'Oven Bake — Mediterranean Chicken',
                runs: '20 minutes · local timer daemon',
                body: 'Countdown initialized at 20:00. Will sound audio notification alert on completion.',
                resultText: '✓ 20-minute timer active in system tray',
                undone: false
              }
            ]
          };
        } else if (lower.indexOf('grocery') !== -1) {
          return {
            type: 'agent',
            sender: 'CHEF',
            text: 'Compiled your grocery checklist based on upcoming meals in Apple Reminders:',
            cards: [
              {
                type: 'reminder',
                id: 'card-chef-grocery-' + Date.now(),
                head: 'Add to Reminders',
                badges: ['GROCERIES', 'APPLE REMINDERS'],
                risk: 'Reaches nobody — nothing leaves your machine.',
                title: 'Grocery: Greek feta, baby spinach, cold-pressed olive oil, lemons',
                time: 'Tomorrow, 5:00 PM',
                completed: false
              }
            ]
          };
        } else {
          return {
            type: 'agent',
            sender: 'CHEF',
            text: 'Macro analysis for tonight: 520 kcal · 48g Protein · 18g Fat · 14g Carbs. High protein target achieved with Mediterranean herbs and olive oil.',
            cards: [
              {
                type: 'automation',
                id: 'card-chef-macro-' + Date.now(),
                head: 'Nutritional summary',
                badge: 'HEALTH',
                statusBadge: 'DONE',
                name: 'Daily nutrition balance',
                runs: 'Logged to ~/Library/Chitragupta/health.sqlite',
                body: 'Dinner: 520 kcal, 48g protein. Daily target: 140g protein (122g achieved so far).',
                resultText: '✓ Nutrition logged to on-device health record',
                undone: false
              }
            ]
          };
        }
      } else if (agentId === 'social-media-manager') {
        if (lower.indexOf('thread') !== -1 || lower.indexOf('tweet') !== -1) {
          return {
            type: 'agent',
            sender: 'social media manager',
            text: 'Drafted a 3-part thread summarizing Chitragupta’s local-first architecture:\n\n1/3 Most AI tools suffer from amnesia. Every tab close wipes your context. Chitragupta fixes this with an on-device local brain in ~/Library/Chitragupta.\n2/3 2.8ms sqlite-vec recall on Apple Silicon Metal GPU. Zero cloud data leaks.\n3/3 Your context belongs to you. Private, fast, offline.',
            cards: [
              {
                type: 'automation',
                id: 'card-social-thread-' + Date.now(),
                head: 'Save post draft',
                badge: 'SOCIAL DRAFT',
                statusBadge: 'DONE',
                name: '3-Part Local AI Thread',
                runs: 'Ready for review',
                body: 'Thread saved locally in your drafts directory. Outbound posting requires your explicit approval.',
                resultText: '✓ Post draft saved to ~/Library/Chitragupta/drafts.json',
                undone: false
              }
            ]
          };
        } else {
          return {
            type: 'agent',
            sender: 'social media manager',
            text: 'Drafted LinkedIn announcement version highlighting privacy compliance, offline capabilities, and local-first memory retention without cloud LLM telemetry.',
            cards: [
              {
                type: 'automation',
                id: 'card-social-li-' + Date.now(),
                head: 'Draft announcement',
                badge: 'LINKEDIN DRAFT',
                statusBadge: 'DONE',
                name: 'Chitragupta v1.0 Launch Summary',
                runs: 'Ready for review',
                body: 'Draft ready: "Why our team built a local-first AI assistant that runs 100% on Apple Silicon..."',
                resultText: '✓ Draft prepared for your review',
                undone: false
              }
            ]
          };
        }
      } else {
        // fashion-stylist
        return {
          type: 'agent',
          sender: 'fashion manager and stylist',
          text: 'Weather today in your location: 24°C and breezy. Recommended outfit: structured linen overshirt, tapered neutral chinos, and minimal white leather sneakers.',
          cards: [
            {
              type: 'automation',
              id: 'card-stylist-' + Date.now(),
              head: 'Style recommendation',
              badge: 'STYLE LOG',
              statusBadge: 'DONE',
              name: 'Casual Day Look — Neutral Tones',
              runs: 'Logged to ~/Library/Chitragupta/wardrobe.sqlite',
              body: 'Outfit selected from 42 scanned items in your local wardrobe archive.',
              resultText: '✓ Wardrobe suggestions indexed locally',
              undone: false
            }
          ]
        };
      }
    }

    // Bind sidebar agent click events
    if (agentListEl) {
      var agentButtons = agentListEl.querySelectorAll('.app-agent-item');
      agentButtons.forEach(function (btn) {
        btn.addEventListener('click', function () {
          var targetAgent = btn.getAttribute('data-agent');
          if (targetAgent) switchAgent(targetAgent);
        });
      });
    }

    // New Agent button in sidebar
    var newAgentBtn = document.getElementById('newAgentBtn');
    if (newAgentBtn) {
      newAgentBtn.addEventListener('click', function () {
        showToast('Create Agent: In the native Mac app, configure custom system prompt, memory tools, and autonomy settings.');
      });
    }

    // Collapse sidebar button
    var collapseSidebarBtn = document.getElementById('collapseSidebarBtn');
    if (collapseSidebarBtn) {
      collapseSidebarBtn.addEventListener('click', function () {
        showToast('Sidebar collapse: In the desktop app, collapses into an icon-only quick rail.');
      });
    }

    // Lower rail navigation items
    var snavButtons = document.querySelectorAll('.app-snav');
    snavButtons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var txEl = btn.querySelector('.app-snav-tx');
        var label = txEl ? txEl.textContent.trim() : 'Tab';
        if (label === 'Inbox') {
          showToast('Inbox: In the desktop app, browses connected Gmail & Apple Mail queues on-device.');
        } else if (label === 'Actions') {
          showToast('Actions: Logs recent local sandbox tool executions, file edits, and system actions.');
        } else if (label === 'Brain') {
          showToast('Brain: Explores persistent local knowledge graph (3,950 memories, 3,455 entities in SQLite).');
        } else if (label === 'Browser') {
          showToast('Browser: Launches isolated headless Chromium sandbox profile for automated web tasks.');
        }
      });
    });

    // Account & settings buttons
    var userChip = document.querySelector('.app-user-chip');
    if (userChip) {
      userChip.addEventListener('click', function () {
        showToast('Account: Local user profile with encrypted macOS Keychain credentials.');
      });
    }
    var themeBtn = document.querySelector('.app-theme-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', function () {
        showToast('Settings: Configure local models (Ollama, LM Studio), API keys, and system connectors.');
      });
    }

    // Reset / Clear chat button
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        conversationHistories[activeAgentId] = AGENTS_DATA[activeAgentId].getInitialChat();
        renderMessages();
        showToast('Cleared chat for ' + AGENTS_DATA[activeAgentId].name + '.');
      });
    }

    // Read-only / disabled interaction bar prompt hint
    if (appInputEl) {
      appInputEl.addEventListener('click', function () {
        showToast('Live preview: Select an agent from the left sidebar to inspect their workflows and local actions.');
      });
      appInputEl.addEventListener('keydown', function (e) {
        e.preventDefault();
        showToast('Live preview: Select an agent from the left sidebar to inspect their workflows and local actions.');
      });
    }

    // Initialize with personal-manager (matching exact screenshot)
    switchAgent('personal-manager');
  }

  // Initialize workspace display on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWorkspaceDisplay);
  } else {
    initWorkspaceDisplay();
  }


})();
