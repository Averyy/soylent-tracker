/**
 * Soylent Wallpaper Engine
 *
 * Renders animated Soylent bottle silhouettes across a full-viewport
 * 2D canvas. Bottles randomly disappear and reappear with spring-bounce
 * animations.
 *
 * Rendering: each flavor is pre-rasterized once into a small sprite
 * bitmap (at device resolution, supersampled 2x), and every frame is a
 * clear + drawImage pass. The previous implementation animated inline
 * styles on SVG child elements, which Chrome and Firefox cannot
 * GPU-composite — every frame forced a main-thread repaint of the whole
 * full-viewport vector layer. Canvas sprite blits keep the per-frame
 * cost trivial in all browsers. Frames are only drawn while an
 * animation is actually running (render-on-demand, no idle rAF loop).
 *
 * Depends on: anime.js v4 (global `anime` object) — drives the numeric
 * tweens on plain JS bottle objects.
 *
 * Usage:
 *   var ctrl = SoylentWallpaper.init(canvasElement, options?)
 *   ctrl.set(key, value)   // live parameter update
 *   ctrl.regenerate()      // rebuild grid
 *   ctrl.destroy()         // clean teardown
 *   ctrl.bottleCount       // current count
 */
(function() {
  'use strict';

  // ── Flavor palettes (light + dark) ──────────────────────

  // Page background colors for bottle body fill (occludes bottles behind)
  var BG = { light: '#F7F7F6', dark: '#131316' };

  var PALETTES = {
    light: [
      { label: '#F7E2E4', body: '#DCDEE0', cap: '#D0D1D2' }, // strawberry
      { label: '#F6F3E8', body: '#DCDEE0', cap: '#D0D1D2' }, // banana
      { label: '#E5E0DE', body: '#DCDEE0', cap: '#D0D1D2' }, // chocolate
      { label: '#EEF5EF', body: '#DCDEE0', cap: '#D0D1D2' }, // mint
      { label: '#E5EBF3', body: '#DCDEE0', cap: '#D0D1D2' }, // blue
    ],
    dark: [
      { label: '#2D1A1D', body: '#333340', cap: '#303038' }, // strawberry
      { label: '#2A2718', body: '#333340', cap: '#303038' }, // banana
      { label: '#231D19', body: '#333340', cap: '#303038' }, // chocolate
      { label: '#1A251D', body: '#333340', cap: '#303038' }, // mint
      { label: '#1A2230', body: '#333340', cap: '#303038' }, // blue
    ],
  };

  // ── SVG path data (traced from Soylent bottle contour) ──

  var PATHS = {
    label: 'M-5.1,-16.9 C-7,-10 -8.5,-5 -8.5,0 L8.4,0 C8.4,-5 7,-10 4.7,-16.6 Z',
    body:  'M-4.5,-20 L-5.1,-16.9 C-7,-10 -8.5,-5 -8.5,-1.3 L-8.6,21.9 C-8.6,24 -7,25 -5.3,25 L3.9,25 C7,25 8.6,24 8.6,21.2 L8.4,1.2 C8.4,-5 7,-10 4.7,-16.6 L4.2,-20',
    cap:   'M-3.9,-25 Q-5.6,-25 -5.6,-23.8 L-5.7,-18.1 L-5.1,-16.9 L4.7,-16.6 L5.2,-18.4 L5,-24 Q5,-25 2.4,-24.9 Z',
  };

  // ── Sprite geometry ─────────────────────────────────────
  //
  // Bottle local coords span x[-9.1, 9.1], y[-25, 25.5] including the
  // 1px body stroke. Sprites pad that to a 20x52 unit box with the
  // bottle's local origin (its transform anchor) at (10, 26).
  // SUPERSAMPLE renders sprites at 2x device resolution so rotated /
  // spring-overshoot-scaled draws stay crisp.

  var SPRITE_W = 20, SPRITE_H = 52;
  var SPRITE_AX = 10, SPRITE_AY = 26;
  var SUPERSAMPLE = 2;

  var PATH2D = null; // lazily-built Path2D cache (shared across inits)

  function buildSprites(isDark, dpr) {
    if (!PATH2D) {
      PATH2D = {
        label: new Path2D(PATHS.label),
        body: new Path2D(PATHS.body),
        cap: new Path2D(PATHS.cap),
      };
    }
    var u = dpr * SUPERSAMPLE; // sprite px per bottle unit
    var palettes = isDark ? PALETTES.dark : PALETTES.light;
    var bg = isDark ? BG.dark : BG.light;
    var sprites = [];
    for (var i = 0; i < palettes.length; i++) {
      var p = palettes[i];
      var c = document.createElement('canvas');
      c.width = Math.ceil(SPRITE_W * u);
      c.height = Math.ceil(SPRITE_H * u);
      var sctx = c.getContext('2d');
      sctx.setTransform(u, 0, 0, u, SPRITE_AX * u, SPRITE_AY * u);
      // Same paint order as the old SVG bottle group:
      sctx.fillStyle = bg;          // 1. bg occlusion (solid silhouette)
      sctx.fill(PATH2D.body);
      sctx.fillStyle = p.label;     // 2. colored label
      sctx.fill(PATH2D.label);
      sctx.strokeStyle = p.body;    // 3. outline on top
      sctx.lineWidth = 1;
      sctx.stroke(PATH2D.body);
      sctx.fillStyle = p.cap;       // 4. cap on top
      sctx.fill(PATH2D.cap);
      sprites.push(c);
    }
    return sprites;
  }

  // ── Tunable defaults ────────────────────────────────────

  var DEFAULTS = {
    spacing: 60,
    cycleInterval: 1000,
    batchSize: 5,
    sizeVariation: 0.1,   // ±10% scale range (0.9–1.1)
    rotationRange: 45,    // ±45 degrees
    randomness: 0.5,      // 0–1, controls positional jitter
  };

  // ── Placement: Jittered Hex Grid ────────────────────────
  //
  // Hex grid = most uniform 2D coverage (no gaps by construction).
  // jitterX/jitterY break the geometric regularity. Asymmetric
  // jitter (more X, less Y) prevents overlap when bottles are
  // taller than the row height allows.

  function hexGrid(w, h, spacing, jitterX, jitterY) {
    var points = [];
    var rowH = spacing * 0.866; // sqrt(3)/2 — hex row height
    var margin = spacing;
    var row = 0;
    for (var y = -margin; y < h + margin; y += rowH) {
      var offset = (row & 1) ? spacing * 0.5 : 0;
      for (var x = -margin + offset; x < w + margin; x += spacing) {
        points.push({
          x: x + (Math.random() * 2 - 1) * jitterX,
          y: y + (Math.random() * 2 - 1) * jitterY,
        });
      }
      row++;
    }
    return points;
  }

  // ── Fisher-Yates shuffle ────────────────────────────────

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
    }
  }

  // ── Main init ───────────────────────────────────────────

  function init(canvasEl, userOpts) {
    // One engine per canvas: a second init() (e.g. a stray double call
    // from the page) must not leave two engines alternately clearing
    // and redrawing different grids on the same context.
    if (canvasEl._soylentWallpaper) canvasEl._soylentWallpaper.destroy();

    var opts = {};
    var key;
    for (key in DEFAULTS) opts[key] = DEFAULTS[key];
    if (userOpts) for (key in userOpts) opts[key] = userOpts[key];

    var ctx = canvasEl.getContext('2d');
    var bottles = [];
    var sprites = [];
    var dpr = 1;
    var drawQueued = false;
    var intervalId = null;
    var resizeTimer = null;
    var rebuildTimer = null;
    var isDark = document.documentElement.classList.contains('dark');
    var destroyed = false;
    var motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    var reducedMotion = motionQuery.matches;
    var dprQuery = null;

    // anime.js v4 spring: takes { mass, stiffness, damping, velocity } object
    var springEase = anime.spring({ mass: 1, stiffness: 80, damping: 10, velocity: 0 });

    function getSize() {
      return { w: canvasEl.clientWidth || window.innerWidth, h: canvasEl.clientHeight || window.innerHeight };
    }

    // ── Render-on-demand draw loop ──
    //
    // anime.js tweens plain bottle objects; onUpdate coalesces to at
    // most one canvas redraw per engine tick via a microtask. The
    // microtask runs after anime's rAF callback finishes updating ALL
    // active tweens but within the same rendering frame — drawing with
    // fresh values and no added latency. (Scheduling our own rAF here
    // instead would fire one frame late and, interleaved with anime's
    // persistent rAF, halve the presented frame rate to ~30fps.)
    // When no animation is running, nothing is drawn at all.

    function requestRender() {
      if (drawQueued || destroyed) return;
      drawQueued = true;
      queueMicrotask(function() {
        drawQueued = false;
        if (!destroyed) draw();
      });
    }

    function draw() {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
      for (var i = 0; i < bottles.length; i++) {
        var b = bottles[i];
        if (b.scale <= 0 || b.opacity <= 0) continue;
        var s = b.baseScale * b.scale;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.translate(b.x, b.y);
        ctx.rotate(b.rad);
        ctx.scale(s, s);
        // spring ease overshoots past 1; canvas ignores out-of-range alpha
        ctx.globalAlpha = b.opacity > 1 ? 1 : b.opacity;
        ctx.drawImage(sprites[b.flavorIdx], -SPRITE_AX, -SPRITE_AY, SPRITE_W, SPRITE_H);
      }
      ctx.globalAlpha = 1;
    }

    // ── Build bottles ──

    function buildBottles() {
      dpr = window.devicePixelRatio || 1;
      var size = getSize();
      canvasEl.width = Math.round(size.w * dpr);
      canvasEl.height = Math.round(size.h * dpr);
      ctx.imageSmoothingQuality = 'high';
      sprites = buildSprites(isDark, dpr);
      bottles = [];

      // Bottles are ~17px wide, ~50px tall. Equal jitter in both axes;
      // overlap is allowed — bottles can stack (paint order occludes).
      var r = opts.randomness;
      var jitterX = opts.spacing * 0.4 * r;
      var jitterY = opts.spacing * 0.4 * r;
      var points = hexGrid(size.w, size.h, opts.spacing, jitterX, jitterY);
      shuffle(points);

      for (var i = 0; i < points.length; i++) {
        bottles.push({
          x: points[i].x,
          y: points[i].y,
          rad: (Math.random() * 2 - 1) * opts.rotationRange * Math.PI / 180,
          baseScale: 1 + (Math.random() * 2 - 1) * opts.sizeVariation,
          flavorIdx: Math.floor(Math.random() * PALETTES.light.length),
          hidden: false,
          scale: 1,
          opacity: 1,
        });
      }

      // Seed the standing vacancy pool. Reappearances pick random empty
      // slots, so some slots must start empty — otherwise every addition
      // would land exactly where a removal just happened. Bottles are in
      // shuffled-point order, so the first N form a random subset.
      // Skipped under reduced motion: the churn loop that refills slots
      // never runs there, so seeded gaps would be permanent.
      if (!reducedMotion) {
        var seed = targetHidden();
        for (var s = 0; s < seed && s < bottles.length; s++) {
          bottles[s].hidden = true;
          bottles[s].scale = 0;
          bottles[s].opacity = 0;
        }
      }
      draw();
    }

    // ── Vacancy pool sizing ──
    //
    // Standing count of empty slots the churn spreads across. Scales
    // with batch size so a freshly vacated slot only rarely refills
    // immediately (chance ≈ batchSize / pool ≈ 25%), bounded below by
    // 6% of the grid and above by 25% so it never looks sparse.

    function targetHidden() {
      var n = bottles.length;
      if (!n) return 0;
      return Math.min(Math.max(opts.batchSize * 4, Math.round(n * 0.06)), Math.round(n * 0.25));
    }

    // Re-randomize a bottle's look before it reappears, so a return —
    // even to a previously used slot — reads as a brand-new bottle.

    function rerollLook(b) {
      b.flavorIdx = Math.floor(Math.random() * PALETTES.light.length);
      b.baseScale = 1 + (Math.random() * 2 - 1) * opts.sizeVariation;
      b.rad = (Math.random() * 2 - 1) * opts.rotationRange * Math.PI / 180;
    }

    // ── Animation: hide a bottle ──

    function hideBottle(b, delay) {
      b.hidden = true;
      anime.animate(b, {
        opacity: 0,
        scale: 0,
        duration: 600,
        delay: delay || 0,
        ease: 'in(3)',
        onUpdate: requestRender,
      });
    }

    // ── Animation: show a bottle (spring bounce) ──

    function showBottle(b, delay) {
      b.hidden = false;
      anime.animate(b, {
        opacity: [0, 1],
        scale: [0, 1],
        delay: delay || 0,
        ease: springEase,
        onUpdate: requestRender,
      });
    }

    // ── Animation loop tick ──
    //
    // Each tick: spring back enough random empty slots to hold the
    // vacancy pool at its target, then hide batchSize random visible
    // bottles. Removals and additions are both uniform-random and
    // decoupled — a vacated slot sits in the pool and only refills by
    // chance, not on the next tick. Each bottle gets a random delay
    // (0–400ms) so they don't all pop at once.

    function tick() {
      if (destroyed || reducedMotion) return;

      var hidden = [];
      var visible = [];
      for (var i = 0; i < bottles.length; i++) {
        if (bottles[i].hidden) hidden.push(bottles[i]);
        else visible.push(bottles[i]);
      }

      // Reappear phase: refill toward the vacancy target at random
      // empty slots (this tick's hides aren't in the pool yet, so a
      // bottle never bounces straight back)
      shuffle(hidden);
      var toShow = Math.min(hidden.length, Math.max(0, hidden.length + opts.batchSize - targetHidden()));
      for (var j = 0; j < toShow; j++) {
        rerollLook(hidden[j]);
        showBottle(hidden[j], Math.random() * 400);
      }

      // Hide phase: pick batchSize random visible bottles
      shuffle(visible);
      var toHide = Math.min(opts.batchSize, visible.length);
      for (var h = 0; h < toHide; h++) {
        hideBottle(visible[h], Math.random() * 400);
      }
    }

    // ── Loop management ──

    function startLoop() {
      stopLoop();
      if (!reducedMotion && !destroyed) {
        intervalId = setInterval(tick, opts.cycleInterval);
      }
    }

    function stopLoop() {
      if (intervalId !== null) {
        clearInterval(intervalId);
        intervalId = null;
      }
    }

    // ── Dark mode observer ──

    var darkObserver = new MutationObserver(function(mutations) {
      for (var i = 0; i < mutations.length; i++) {
        if (mutations[i].attributeName === 'class') {
          var nowDark = document.documentElement.classList.contains('dark');
          if (nowDark !== isDark) {
            isDark = nowDark;
            sprites = buildSprites(isDark, dpr);
            draw();
          }
        }
      }
    });
    darkObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    // ── Resize handler (debounced 250ms) ──

    function onResize() {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function() {
        if (!destroyed) {
          buildBottles();
          startLoop();
        }
      }, 250);
    }
    window.addEventListener('resize', onResize);

    // ── Device-pixel-ratio watcher ──
    //
    // Sprites and the canvas backing store are sized for the current
    // DPR. Moving the window to a display with a different DPR without
    // a resize (same CSS size) would leave them blurry — rebuild.

    function watchDpr() {
      if (dprQuery) dprQuery.removeEventListener('change', onDprChange);
      dprQuery = window.matchMedia('(resolution: ' + (window.devicePixelRatio || 1) + 'dppx)');
      dprQuery.addEventListener('change', onDprChange);
    }

    function onDprChange() {
      if (destroyed) return;
      buildBottles();
      startLoop();
      watchDpr();
    }
    watchDpr();

    // ── Reduced motion listener ──

    function onMotionChange(e) {
      reducedMotion = e.matches;
      if (reducedMotion) {
        stopLoop();
        for (var i = 0; i < bottles.length; i++) {
          bottles[i].hidden = false;
          bottles[i].scale = 1;
          bottles[i].opacity = 1;
        }
        draw();
      } else {
        startLoop();
      }
    }
    motionQuery.addEventListener('change', onMotionChange);

    // ── Visibility change — pause in background tabs ──

    function onVisibilityChange() {
      if (document.hidden) {
        stopLoop();
      } else if (!reducedMotion && !destroyed) {
        startLoop();
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange);

    // ── Build & start ──

    buildBottles();
    startLoop();

    // ── Public API ──

    var api = {
      set: function(k, v) {
        opts[k] = v;
        if (k === 'spacing' || k === 'sizeVariation' || k === 'rotationRange' || k === 'randomness') {
          if (rebuildTimer) clearTimeout(rebuildTimer);
          rebuildTimer = setTimeout(function() {
            if (!destroyed) {
              buildBottles();
              startLoop();
            }
          }, 80);
        } else if (k === 'cycleInterval') {
          startLoop();
        }
      },

      regenerate: function() {
        if (destroyed) return;
        buildBottles();
        startLoop();
      },

      destroy: function() {
        destroyed = true;
        stopLoop();
        if (resizeTimer) clearTimeout(resizeTimer);
        if (rebuildTimer) clearTimeout(rebuildTimer);
        darkObserver.disconnect();
        window.removeEventListener('resize', onResize);
        motionQuery.removeEventListener('change', onMotionChange);
        if (dprQuery) dprQuery.removeEventListener('change', onDprChange);
        document.removeEventListener('visibilitychange', onVisibilityChange);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
        bottles = [];
        if (canvasEl._soylentWallpaper === api) canvasEl._soylentWallpaper = null;
      },

      get bottleCount() {
        return bottles.length;
      },
    };

    canvasEl._soylentWallpaper = api;
    return api;
  }

  window.SoylentWallpaper = { init: init };
})();
