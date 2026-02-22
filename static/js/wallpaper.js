/**
 * Soylent Wallpaper Engine
 *
 * Renders animated Soylent bottle silhouettes across a full-viewport SVG.
 * Bottles randomly disappear and reappear with spring-bounce animations.
 *
 * Depends on: anime.js v4 (global `anime` object)
 *
 * Usage:
 *   var ctrl = SoylentWallpaper.init(svgElement, options?)
 *   ctrl.set(key, value)   // live parameter update
 *   ctrl.regenerate()      // rebuild grid
 *   ctrl.destroy()         // clean teardown
 *   ctrl.bottleCount       // current count
 */
(function() {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

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

  // ── Bottle factory ──────────────────────────────────────

  function createBottle(svg, x, y, flavorIdx, isDark, baseScale, rotation) {
    var palette = isDark ? PALETTES.dark[flavorIdx] : PALETTES.light[flavorIdx];

    var outer = document.createElementNS(SVG_NS, 'g');
    outer.setAttribute('transform',
      'translate(' + x.toFixed(1) + ',' + y.toFixed(1) + ') ' +
      'rotate(' + rotation.toFixed(1) + ') ' +
      'scale(' + baseScale.toFixed(3) + ')'
    );

    // Inner group: animated via CSS opacity + transform (no SVG transform conflict)
    var inner = document.createElementNS(SVG_NS, 'g');
    inner.style.transformOrigin = '0px 0px';

    // Solid silhouette (bg color fill, no stroke) — occludes bottles behind
    var bodyFill = document.createElementNS(SVG_NS, 'path');
    bodyFill.setAttribute('d', PATHS.body);
    bodyFill.setAttribute('fill', isDark ? BG.dark : BG.light);

    var label = document.createElementNS(SVG_NS, 'path');
    label.setAttribute('d', PATHS.label);
    label.setAttribute('fill', palette.label);

    // Outline only (no fill) — draws on top of the label
    var body = document.createElementNS(SVG_NS, 'path');
    body.setAttribute('d', PATHS.body);
    body.setAttribute('fill', 'none');
    body.setAttribute('stroke', palette.body);
    body.setAttribute('stroke-width', '1');

    var cap = document.createElementNS(SVG_NS, 'path');
    cap.setAttribute('d', PATHS.cap);
    cap.setAttribute('fill', palette.cap);

    inner.appendChild(bodyFill);  // 1. bg occlusion
    inner.appendChild(label);     // 2. colored label
    inner.appendChild(body);      // 3. outline on top
    inner.appendChild(cap);       // 4. cap on top
    outer.appendChild(inner);
    svg.appendChild(outer);

    return {
      outer: outer,
      inner: inner,
      bodyFill: bodyFill,
      label: label,
      body: body,
      cap: cap,
      flavorIdx: flavorIdx,
      hidden: false,
    };
  }

  // ── Recolor all bottles (dark mode switch) ──────────────

  function recolorBottles(bottles, isDark) {
    var bgFill = isDark ? BG.dark : BG.light;
    for (var i = 0; i < bottles.length; i++) {
      var b = bottles[i];
      var p = isDark ? PALETTES.dark[b.flavorIdx] : PALETTES.light[b.flavorIdx];
      b.bodyFill.setAttribute('fill', bgFill);
      b.label.setAttribute('fill', p.label);
      b.body.setAttribute('stroke', p.body);
      b.cap.setAttribute('fill', p.cap);
    }
  }

  // ── Fisher-Yates shuffle ────────────────────────────────

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
    }
  }

  // ── Main init ───────────────────────────────────────────

  function init(svgEl, userOpts) {
    var opts = {};
    var key;
    for (key in DEFAULTS) opts[key] = DEFAULTS[key];
    if (userOpts) for (key in userOpts) opts[key] = userOpts[key];

    var bottles = [];
    var intervalId = null;
    var resizeTimer = null;
    var rebuildTimer = null;
    var isDark = document.documentElement.classList.contains('dark');
    var destroyed = false;
    var motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    var reducedMotion = motionQuery.matches;

    // anime.js v4 spring: takes { mass, stiffness, damping, velocity } object
    var springEase = anime.spring({ mass: 1, stiffness: 80, damping: 10, velocity: 0 });

    function getSize() {
      return { w: svgEl.clientWidth || window.innerWidth, h: svgEl.clientHeight || window.innerHeight };
    }

    // ── Build bottles ──

    function buildBottles() {
      while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);
      bottles = [];

      var size = getSize();
      svgEl.setAttribute('viewBox', '0 0 ' + size.w + ' ' + size.h);
      // Bottles are ~17px wide, ~50px tall. Asymmetric jitter:
      // X can be generous (bottles are narrow), Y must be tight (bottles are tall).
      // Overlap ON: equal jitter in both axes, bottles can stack.
      var r = opts.randomness;
      var jitterX = opts.spacing * 0.4 * r;
      var jitterY = opts.spacing * 0.4 * r;
      var points = hexGrid(size.w, size.h, opts.spacing, jitterX, jitterY);
      shuffle(points);

      for (var i = 0; i < points.length; i++) {
        var flavorIdx = Math.floor(Math.random() * 5);
        var baseScale = 1 + (Math.random() * 2 - 1) * opts.sizeVariation;
        var rotation = (Math.random() * 2 - 1) * opts.rotationRange;
        bottles.push(createBottle(svgEl, points[i].x, points[i].y, flavorIdx, isDark, baseScale, rotation));
      }
    }

    // ── Animation: hide a bottle ──

    function hideBottle(b, delay) {
      b.hidden = true;
      anime.animate(b.inner, {
        opacity: 0,
        scale: 0,
        duration: 600,
        delay: delay || 0,
        ease: 'in(3)',
      });
    }

    // ── Animation: show a bottle (spring bounce) ──

    function showBottle(b, delay) {
      b.hidden = false;
      anime.animate(b.inner, {
        opacity: [0, 1],
        scale: [0, 1],
        delay: delay || 0,
        ease: springEase,
      });
    }

    // ── Animation loop tick ──
    //
    // Each tick: show hidden bottles (60% chance each), then
    // hide batchSize random visible ones. Each bottle gets a
    // random delay (0–400ms) so they don't all pop at once.

    function tick() {
      if (destroyed || reducedMotion) return;

      var hidden = [];
      var visible = [];
      for (var i = 0; i < bottles.length; i++) {
        if (bottles[i].hidden) hidden.push(bottles[i]);
        else visible.push(bottles[i]);
      }

      // Reappear phase: hidden bottles get 60% chance to spring back
      for (var j = 0; j < hidden.length; j++) {
        if (Math.random() < 0.6) showBottle(hidden[j], Math.random() * 400);
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
      if (!reducedMotion) {
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
            recolorBottles(bottles, isDark);
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

    // ── Reduced motion listener ──

    function onMotionChange(e) {
      reducedMotion = e.matches;
      if (reducedMotion) {
        stopLoop();
        for (var i = 0; i < bottles.length; i++) {
          bottles[i].hidden = false;
          bottles[i].inner.style.opacity = '1';
          bottles[i].inner.style.transform = 'scale(1)';
        }
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

    return {
      set: function(k, v) {
        opts[k] = v;
        if (k === 'spacing' || k === 'sizeVariation' || k === 'rotationRange' || k === 'randomness') {
          if (rebuildTimer) clearTimeout(rebuildTimer);
          rebuildTimer = setTimeout(function() {
            buildBottles();
            startLoop();
          }, 80);
        } else if (k === 'cycleInterval') {
          startLoop();
        }
      },

      regenerate: function() {
        buildBottles();
        startLoop();
      },

      destroy: function() {
        destroyed = true;
        stopLoop();
        darkObserver.disconnect();
        window.removeEventListener('resize', onResize);
        motionQuery.removeEventListener('change', onMotionChange);
        document.removeEventListener('visibilitychange', onVisibilityChange);
        while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);
        bottles = [];
      },

      get bottleCount() {
        return bottles.length;
      },
    };
  }

  window.SoylentWallpaper = { init: init };
})();
