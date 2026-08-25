/* Slow WebGL mesh gradient behind the dashboard, tinted by the dominant genres
   currently on screen — the backdrop reports what the charts report.

   Hand-rolled (no shader lib): four moving blobs, quarter-resolution behind a CSS
   blur, 20fps. Stops entirely on hidden tab, prefers-reduced-motion, or Tweaks off.

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
  };

  const FRAME_MS = 50;  // 20fps — invisible on something drifting this slowly
  const SCALE = 0.25;   // render resolution; the CSS blur hides the rest
  const FADE = 0.035;   // per-tick approach to a new palette (~1.5s settle)

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
    if (!init()) return;
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
