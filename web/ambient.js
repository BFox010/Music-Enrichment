/* Slow WebGL mesh gradient behind the dashboard, tinted by the dominant genres
   currently on screen — the backdrop reports what the charts report.

   Hand-rolled (no shader lib): four moving blobs, quarter-resolution behind a CSS
   blur, 20fps. Stops entirely on hidden tab, prefers-reduced-motion, or Tweaks off.

   Above it sits a second, much cheaper layer: rare drifting sprites (see "rare
   drifting visitors" below), gated on the same switches.

   Loaded as <script> before app.bundle.js; React talks to it via window.MLAmbient. */
(function () {
  "use strict";

  const VERT = `
    attribute vec2 aPos;
    void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
  `;

  // Four blobs on lissajous paths, inverse-square-ish weighted, normalised, then
  // gamma-encoded. Colours arrive in linear light (oklchToLinear), so the blend
  // happens where blending is meaningful.
  const FRAG = `
    precision mediump float;
    uniform vec2  uRes;
    uniform float uT;
    uniform vec3  uC0, uC1, uC2, uC3;
    void main() {
      float ar = uRes.x / max(uRes.y, 1.0);
      vec2 p = gl_FragCoord.xy / uRes.xy;
      p.x *= ar;
      vec3 col = vec3(0.0);
      float wsum = 0.0;
      for (int i = 0; i < 4; i++) {
        float f = float(i);
        vec2 c = vec2(
          0.5 + 0.40 * sin(uT * 0.061 + f * 2.20),
          0.5 + 0.33 * cos(uT * 0.047 + f * 1.73)
        );
        c.x *= ar;
        vec3 ci = i == 0 ? uC0 : (i == 1 ? uC1 : (i == 2 ? uC2 : uC3));
        float d = distance(p, c);
        float w = exp(-d * d * 3.0);
        col += ci * w;
        wsum += w;
      }
      col /= max(wsum, 0.0001);
      gl_FragColor = vec4(pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2)), 1.0);
    }
  `;

  /* OKLCH → linear sRGB. dashboard.jsx authors the genre palette as
     `oklch(0.72 0.14 <hue>)`, so this is how the charts' colours reach the shader. */
  function oklchToLinear(L, C, H) {
    const h = (H * Math.PI) / 180;
    const a = C * Math.cos(h), b = C * Math.sin(h);
    const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
    const l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
    return [
      clamp01(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
      clamp01(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
      clamp01(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
    ];
  }

  /* The backdrop must read near-black with only a hint of hue. The chart palette
     (L 0.72) is right on a small swatch and far too light across a viewport, so
     every colour is remapped into a narrow dark band — relative ordering between
     hues survives, the range collapses. Chroma is raised because a colour this
     dark loses apparent saturation. */
  const AMBIENT_L_BASE = 0.10;   // darkest a backdrop colour may be
  const AMBIENT_L_SPAN = 0.16;   // ...and how far the lightest may rise above it
  const AMBIENT_C_GAIN = 1.30;

  function ambientTone(L, C, H) {
    return oklchToLinear(AMBIENT_L_BASE + L * AMBIENT_L_SPAN, C * AMBIENT_C_GAIN, H);
  }

  const OKLCH_RE = /oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/i;
  function parseColor(str) {
    const m = OKLCH_RE.exec(String(str || ""));
    return m ? ambientTone(+m[1], +m[2], +m[3]) : null;
  }

  // Midnight theme's violet/steel, so the backdrop looks intentional before any
  // data has been aggregated.
  const FALLBACK = [
    ambientTone(0.42, 0.10, 285),
    ambientTone(0.34, 0.07, 250),
    ambientTone(0.30, 0.06, 200),
    ambientTone(0.38, 0.09, 320),
  ];

  const mqReduce = window.matchMedia("(prefers-reduced-motion: reduce)");

  const st = {
    enabled: localStorage.getItem("ml.ambient") !== "off",
    cur: FALLBACK.map((c) => c.slice()),
    target: FALLBACK.map((c) => c.slice()),
    sig: "",
    gl: null, canvas: null, prog: null, loc: null,
    raf: 0, t0: 0, last: 0, running: false,
    spriteLayer: null, spriteTimer: 0, spriteArmed: false,
  };

  const FRAME_MS = 50;  // 20fps — invisible on something drifting this slowly
  const SCALE = 0.25;   // render resolution; the CSS blur hides the rest
  const FADE = 0.035;   // per-tick approach to a new palette (~1.5s settle)

  /* ── rare drifting visitors ──────────────────────────────────────────────
     A find, not a feature. These are deliberately not shaded into the mesh
     gradient's fragment shader: they are a different concern, and a CSS-animated
     SVG costs nothing while nothing is on screen — no rAF loop, just a timer
     between appearances.

     Rarity lives here and nowhere else. Waits are drawn from an exponential
     distribution so there is no rhythm to notice: a long session yields one,
     maybe two, and the cast is weighted so the cat is the real find. */
  const SPRITE_MEAN_GAP_MS  = 7 * 60 * 1000;  // average wait between sightings
  const SPRITE_MIN_GAP_MS   = 2 * 60 * 1000;  // ...never two on top of each other
  const SPRITE_FIRST_GAP_MS = 75 * 1000;      // ...and never one on the screen you arrive at

  const SVG_JELLYFISH = `
    <svg viewBox="0 0 64 112" fill="none" aria-hidden="true">
      <g class="fx-pulse">
        <path d="M4 40C4 20 16 6 32 6s28 14 28 34c0 6-4 9-10 9H14c-6 0-10-3-10-9Z" fill="currentColor" opacity=".5"/>
        <path d="M4 40C4 20 16 6 32 6s28 14 28 34" stroke="currentColor" stroke-width="2.2" opacity=".75"/>
        <ellipse cx="23" cy="25" rx="5" ry="7.5" fill="#fff" opacity=".14"/>
      </g>
      <g class="fx-trail" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" opacity=".45">
        <path d="M15 49c-2 12 4 18 1 30s3 17 1 25"/>
        <path d="M26 49c-1 14 3 20 0 30s2 16 1 24"/>
        <path d="M38 49c1 13-3 19 0 30s-2 17-1 25"/>
        <path d="M49 49c2 12-4 18-1 30s-3 16-1 23"/>
      </g>
    </svg>`;

  const SVG_BUBBLES = `
    <svg viewBox="0 0 72 124" fill="none" aria-hidden="true">
      <circle class="fx-bub" cx="25" cy="98" r="19" fill="currentColor" opacity=".36"/>
      <circle class="fx-bub" cx="48" cy="60" r="13" fill="currentColor" opacity=".3"/>
      <circle class="fx-bub" cx="20" cy="31" r="8.5" fill="currentColor" opacity=".26"/>
      <circle class="fx-bub" cx="45" cy="11" r="5" fill="currentColor" opacity=".22"/>
      <circle cx="18" cy="91" r="6" fill="#fff" opacity=".12"/>
      <circle cx="44" cy="55" r="4" fill="#fff" opacity=".1"/>
    </svg>`;

  /* Full-bodied and floating, not a head in a bubble: minimal shapes, one
     helmet, a tail that flicks (fx-tail) as it drifts past. */
  const SVG_HELMET_CAT = `
    <svg viewBox="0 0 212 148" fill="none" aria-hidden="true">
      <path class="fx-tail" d="M162 74c17-3 32-16 33-32 1-9-5-15-11-12-5 2-6 9-2 12"
            stroke="currentColor" stroke-width="8" stroke-linecap="round" opacity=".45"/>
      <g stroke="currentColor" stroke-width="13" stroke-linecap="round" opacity=".45">
        <path d="M97 94l-9 21"/><path d="M116 98l-3 22"/>
        <path d="M144 96l8 20"/><path d="M158 89l13 17"/>
      </g>
      <path d="M78 64c6-15 27-23 47-23 27 0 46 15 46 35 0 20-19 33-46 33-25 0-45-11-47-28Z"
            fill="currentColor" opacity=".5"/>
      <g stroke="currentColor" stroke-width="3" stroke-linecap="round" opacity=".7">
        <path d="M110 45c3 6 3 13 1 18"/><path d="M127 43c3 6 3 14 1 19"/>
        <path d="M144 47c3 6 3 13 1 18"/>
      </g>
      <path d="M37 45 35 30l17 8Z" fill="currentColor" opacity=".5"/>
      <path d="M71 43 75 30l-17 9Z" fill="currentColor" opacity=".5"/>
      <ellipse cx="54" cy="65" rx="28" ry="26" fill="currentColor" opacity=".5"/>
      <g fill="#0a0a11" opacity=".7">
        <ellipse cx="44" cy="62" rx="2.9" ry="3.5"/><ellipse cx="64" cy="62" rx="2.9" ry="3.5"/>
        <path d="M54 71.5 50.3 75h7.4Z"/>
      </g>
      <g stroke="currentColor" stroke-width="1.7" stroke-linecap="round" opacity=".5">
        <path d="M40 75H26M40 79l-13 5M68 75h14M68 79l13 5"/>
      </g>
      <ellipse cx="87" cy="46" rx="9" ry="7" transform="rotate(-26 87 46)"
               stroke="currentColor" stroke-width="2.6" opacity=".5"/>
      <circle cx="54" cy="61" r="38" fill="currentColor" opacity=".1"/>
      <circle cx="54" cy="61" r="38" stroke="currentColor" stroke-width="2.6" opacity=".55"/>
      <path d="M29 49A32 32 0 0 1 48 29" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".2"/>
    </svg>`;

  /* weight: relative odds of being the one that shows up. size: viewport width
     it spans. dur: seconds to cross, randomised inside the range. peak: the
     opacity it holds mid-crossing — these sit over a near-black backdrop, so
     "visible" is a lot less than it sounds. */
  const CAST = [
    { id: "bubbles",   weight: 60, svg: SVG_BUBBLES,     rise: true,  size: [5, 8],   dur: [34, 52], peak: 0.72, sway: "as-wobble", swayDur: 6.5 },
    { id: "jellyfish", weight: 30, svg: SVG_JELLYFISH,   rise: false, size: [5, 7.5], dur: [46, 70], peak: 0.72, sway: "as-bob",    swayDur: 4.5 },
    { id: "cat",       weight: 10, svg: SVG_HELMET_CAT,  rise: false, size: [6, 8.5], dur: [55, 80], peak: 0.72, sway: "as-tumble", swayDur: 9 },
  ];
  const CAST_WEIGHT = CAST.reduce((n, c) => n + c.weight, 0);

  const rand = (lo, hi) => lo + Math.random() * (hi - lo);

  function spriteLayer() {
    if (!st.spriteLayer) {
      const el = document.createElement("div");
      el.className = "ambient-sprites";
      el.setAttribute("aria-hidden", "true");
      document.body.appendChild(el);
      st.spriteLayer = el;
    }
    return st.spriteLayer;
  }

  function pickCast(id) {
    if (id) return CAST.find((c) => c.id === id) || null;
    let r = Math.random() * CAST_WEIGHT;
    for (const c of CAST) { r -= c.weight; if (r <= 0) return c; }
    return CAST[CAST.length - 1];
  }

  function spriteInFlight() {
    return !!st.spriteLayer && st.spriteLayer.childElementCount > 0;
  }

  function spawnSprite(id) {
    // Strictly one at a time. The schedule already keeps them apart — the
    // minimum gap is longer than the slowest crossing — but two on screen at
    // once turns a find into a parade, so the invariant is enforced here rather
    // than left as a property of two constants that might be retuned apart.
    if (spriteInFlight()) return;
    const cast = pickCast(id);
    if (!cast) return;
    // Drifters pick a side to enter from: entering from the right is the same
    // crossing played backwards, with the body mirrored so it faces the way it
    // is going.
    const mirror = !cast.rise && Math.random() < 0.5;
    const el = document.createElement("div");
    el.className = "ambient-sprite " + (cast.rise ? "as-riser" : "as-drifter") + (mirror ? " as-mirror" : "");
    if (mirror) el.style.animationDirection = "reverse";
    const dur = rand(cast.dur[0], cast.dur[1]);
    el.style.setProperty("--as-size", rand(cast.size[0], cast.size[1]).toFixed(2) + "vw");
    el.style.setProperty("--as-lane", rand(8, 74).toFixed(1) + "%");
    el.style.setProperty("--as-dur", dur.toFixed(1) + "s");
    el.style.setProperty("--as-peak", String(cast.peak));
    el.style.setProperty("--as-sway", cast.sway);
    el.style.setProperty("--as-sway-dur", cast.swayDur + "s");
    // Finite, not infinite: enough half-cycles to last the crossing and no more.
    el.style.setProperty("--as-sway-n", String(Math.max(1, Math.round(dur / cast.swayDur))));
    el.innerHTML = `<div class="as-body">${cast.svg}</div>`;
    // The sway animation ends first and also bubbles; only the crossing means done.
    el.addEventListener("animationend", (e) => { if (e.target === el) el.remove(); });
    spriteLayer().appendChild(el);
  }

  function spritesAllowed() {
    return st.enabled && !mqReduce.matches;
  }

  function scheduleSprite(first) {
    clearTimeout(st.spriteTimer);
    st.spriteTimer = 0;
    if (!spritesAllowed()) return;
    // Inverse-transform sample of an exponential wait, shifted past the minimum.
    const gap = first
      ? SPRITE_FIRST_GAP_MS
      : SPRITE_MIN_GAP_MS - Math.log(1 - Math.random()) * (SPRITE_MEAN_GAP_MS - SPRITE_MIN_GAP_MS);
    st.spriteTimer = setTimeout(() => {
      st.spriteTimer = 0;
      // A sprite that crossed an unwatched tab was never seen; skip it and wait
      // again rather than banking sightings nobody gets. Same for one that is
      // somehow still crossing: the next wait is punishment enough.
      if (spritesAllowed() && !document.hidden) spawnSprite();
      scheduleSprite(false);
    }, gap);
  }

  function syncSprites() {
    if (!spritesAllowed()) {
      clearTimeout(st.spriteTimer);
      st.spriteTimer = 0;
      st.spriteArmed = false;
      if (st.spriteLayer) st.spriteLayer.replaceChildren();
      return;
    }
    if (st.spriteTimer) return;
    const first = !st.spriteArmed;
    st.spriteArmed = true;
    scheduleSprite(first);
  }

  function compile(gl, type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) || "shader");
    return sh;
  }

  function init() {
    const canvas = document.createElement("canvas");
    canvas.className = "ambient-bg";
    canvas.setAttribute("aria-hidden", "true");
    const gl = canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, powerPreference: "low-power" });
    if (!gl) return false; // no WebGL: the static --bg-grad in themes.css stands on its own

    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    st.canvas = canvas; st.gl = gl; st.prog = prog;
    st.loc = {
      res: gl.getUniformLocation(prog, "uRes"),
      t: gl.getUniformLocation(prog, "uT"),
      c: [0, 1, 2, 3].map((i) => gl.getUniformLocation(prog, "uC" + i)),
    };
    document.body.appendChild(canvas);
    resize();
    window.addEventListener("resize", resize, { passive: true });
    return true;
  }

  function resize() {
    if (!st.canvas) return;
    const w = Math.max(64, Math.round(window.innerWidth * SCALE));
    const h = Math.max(64, Math.round(window.innerHeight * SCALE));
    if (st.canvas.width === w && st.canvas.height === h) return;
    st.canvas.width = w; st.canvas.height = h;
    st.gl.viewport(0, 0, w, h);
  }

  function draw(now) {
    const gl = st.gl;
    for (let i = 0; i < 4; i++) {
      const c = st.cur[i], t = st.target[i];
      for (let k = 0; k < 3; k++) c[k] += (t[k] - c[k]) * FADE;
      gl.uniform3f(st.loc.c[i], c[0], c[1], c[2]);
    }
    gl.uniform2f(st.loc.res, st.canvas.width, st.canvas.height);
    gl.uniform1f(st.loc.t, (now - st.t0) / 1000);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function tick(now) {
    st.raf = 0;
    if (!st.running) return;
    if (now - st.last >= FRAME_MS) { st.last = now; draw(now); }
    st.raf = requestAnimationFrame(tick);
  }

  function shouldRun() {
    return st.enabled && !mqReduce.matches && !document.hidden && !!st.gl;
  }

  function sync() {
    syncSprites();
    const want = shouldRun();
    if (st.canvas) st.canvas.classList.toggle("off", !st.enabled || mqReduce.matches);
    if (want === st.running) return;
    st.running = want;
    if (want) {
      if (!st.t0) st.t0 = performance.now();
      st.raf = requestAnimationFrame(tick);
    } else if (st.raf) {
      cancelAnimationFrame(st.raf);
      st.raf = 0;
    }
  }

  function start() {
    // Sprites are a plain DOM layer, so they run even where WebGL does not.
    if (!init()) { syncSprites(); return; }
    // One frame even with motion off: the backdrop still carries the library's
    // colours, it just holds still.
    draw(performance.now());
    sync();
  }

  document.addEventListener("visibilitychange", sync);
  mqReduce.addEventListener("change", sync);

  window.MLAmbient = {
    /* Accepts the same oklch() strings the charts are drawn with. Anything
       unparseable is dropped; too few colours are recycled so the four blobs
       always have something to show. */
    setColors(list) {
      const parsed = (list || []).map(parseColor).filter(Boolean);
      const sig = parsed.map((c) => c.map((v) => v.toFixed(3)).join()).join("|");
      if (!parsed.length || sig === st.sig) return;
      st.sig = sig;
      for (let i = 0; i < 4; i++) st.target[i] = parsed[i % parsed.length].slice();
      sync();
    },
    /* Sightings are rare by design, which makes them awkward to look at on
       purpose. Summon one: MLAmbient.summon() for a weighted draw, or
       MLAmbient.summon("cat" | "jellyfish" | "bubbles") for a specific one.
       Honours the ambient and reduced-motion gates, and replaces whatever is
       crossing rather than breaking the one-at-a-time rule. */
    summon(id) {
      if (!spritesAllowed()) return;
      if (st.spriteLayer) st.spriteLayer.replaceChildren();
      spawnSprite(id);
    },
    enabled: () => st.enabled,
    setEnabled(on) {
      st.enabled = !!on;
      localStorage.setItem("ml.ambient", on ? "on" : "off");
      sync();
    },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
