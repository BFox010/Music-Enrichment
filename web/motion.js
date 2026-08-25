/* Shared motion primitives, exposed as window.MOTION. Loaded as <script> before
   app.bundle.js (like data-processing.js). Framework-free: React calls in, never out.

   1. pointer loop — card spotlight + magnetic pull. One listener, one rAF, idle = zero work.
   2. FLIP helpers — re-ranked lists travel to their new order.
   3. View Transitions wrapper for page switches.

   All inert under prefers-reduced-motion; (1) also inert for coarse pointers. */
(function () {
  "use strict";

  /* ── capability + preference gates ── */
  const mqReduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mqFine = window.matchMedia("(hover: hover) and (pointer: fine)");

  const state = {
    reduced: mqReduce.matches,
    fine: mqFine.matches,
    // User-facing kill switch for the pointer effects (Tweaks panel).
    pointerFx: localStorage.getItem("ml.pointerfx") !== "off",
  };
  const pointerActive = () => state.fine && !state.reduced && state.pointerFx;

  /* ── 1. pointer loop: spotlight + magnetism ── */

  /* Scoped to whichever container the cursor is in. That is what makes it cheap:
     one layout read on entry, then pure arithmetic per frame instead of measuring
     the page. It is also the restraint — exactly one card is ever lit. */
  const SCOPE_SEL = ".card, .kpi, .appbar, .sidebar, .filterbar, .drill-panel, .kpis";
  const SPOTLIGHT_SEL = ".card, .kpi";
  const MAGNET_SEL = [
    ".btn", ".icon-btn", ".fchip", ".tagchip", ".glegend",
    ".season.clickable", ".sidenav-item", ".seg button", ".fb-clear",
  ].join(", ");

  // 4px is the ceiling on purpose — at this amplitude the lean reads as
  // responsiveness, not as a toy. RADIUS measures from the element's EDGE, not its
  // centre, so a wide button and a small chip respond at the same distance.
  const RADIUS = 72;
  const STRENGTH = 4;
  const EASE = 0.22;      // per-frame approach to the target (critically-damped feel)
  const SETTLED = 0.05;   // px below which we stop writing styles

  let scopeEl = null;
  let spotEl = null;
  let magnets = [];
  let px = 0, py = 0;
  let raf = 0;

  function clearMagnets() {
    for (const m of magnets) m.el.style.translate = "";
    magnets = [];
  }

  function clearSpotlight() {
    if (spotEl) spotEl.classList.remove("lit");
    spotEl = null;
  }

  function setScope(el) {
    if (el === scopeEl) return;
    clearMagnets();
    clearSpotlight();
    scopeEl = el;
    if (!el) return;
    spotEl = el.matches(SPOTLIGHT_SEL) ? el : null;
    if (spotEl) spotEl.classList.add("lit");
    // One layout read per scope entry, cached while the cursor stays.
    magnets = Array.from(el.querySelectorAll(MAGNET_SEL)).map((node) => {
      const r = node.getBoundingClientRect();
      return {
        el: node,
        l: r.left, t: r.top, r: r.right, b: r.bottom,
        cx: r.left + r.width / 2, cy: r.top + r.height / 2,
        x: 0, y: 0,
      };
    });
  }

  function frame() {
    raf = 0;
    let moving = false;

    if (spotEl) {
      // Custom properties only — the gradient itself is pure CSS.
      const r = spotEl.getBoundingClientRect();
      spotEl.style.setProperty("--mx", (px - r.left).toFixed(1) + "px");
      spotEl.style.setProperty("--my", (py - r.top).toFixed(1) + "px");
    }

    for (const m of magnets) {
      let tx = 0, ty = 0;
      if (scopeEl) {
        // Falloff keys off the gap to the box (zero once the cursor is over it);
        // direction comes from the centre, so an element under the cursor leans
        // toward it instead of going slack.
        const gx = px < m.l ? m.l - px : px > m.r ? px - m.r : 0;
        const gy = py < m.t ? m.t - py : py > m.b ? py - m.b : 0;
        const gap = Math.sqrt(gx * gx + gy * gy);
        if (gap < RADIUS) {
          const dx = px - m.cx, dy = py - m.cy;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d > 0.001) {
            const pull = (1 - gap / RADIUS) * STRENGTH;
            tx = (dx / d) * pull;
            ty = (dy / d) * pull;
          }
        }
      }
      m.x += (tx - m.x) * EASE;
      m.y += (ty - m.y) * EASE;
      if (Math.abs(m.x) < SETTLED && Math.abs(m.y) < SETTLED) {
        if (m.el.style.translate) m.el.style.translate = "";
        m.x = 0; m.y = 0;
      } else {
        // `translate` composes independently of `transform`, so this never fights
        // the hover transforms in themes.css.
        m.el.style.translate = m.x.toFixed(2) + "px " + m.y.toFixed(2) + "px";
        moving = true;
      }
    }

    // Idle means idle — the loop stops rather than spinning at 60fps behind a
    // static page.
    if (moving || spotEl) raf = requestAnimationFrame(frame);
  }

  function kick() {
    if (!raf) raf = requestAnimationFrame(frame);
  }

  function onPointerMove(e) {
    if (!pointerActive()) return;
    px = e.clientX; py = e.clientY;
    const t = e.target;
    setScope(t && t.closest ? t.closest(SCOPE_SEL) : null);
    if (scopeEl) kick();
  }

  function release() {
    setScope(null);
    kick(); // one last frame to unwind whatever was still displaced
  }

  document.addEventListener("pointermove", onPointerMove, { passive: true });
  document.addEventListener("pointerleave", release, { passive: true });
  document.addEventListener("pointerdown", release, { passive: true });
  // Rects go stale on scroll/reflow. Dropping the scope is cheaper than
  // re-measuring; the next pointermove rebuilds it.
  window.addEventListener("scroll", release, { passive: true, capture: true });
  window.addEventListener("resize", release, { passive: true });
  window.addEventListener("blur", release);

  function syncPointerGate() {
    if (!pointerActive()) release();
  }
  mqReduce.addEventListener("change", (e) => { state.reduced = e.matches; syncPointerGate(); });
  mqFine.addEventListener("change", (e) => { state.fine = e.matches; syncPointerGate(); });

  /* ── 2. FLIP: lists that travel to their new order ── */

  /* Used by charts.jsx's ranked lists. The point is legibility, not
     decoration: when a filter re-ranks the top artists you get to watch which
     rows survived and which collapsed, instead of diffing two static frames
     from memory. */
  const FLIP_SEL = "[data-flip-key]";
  const FLIP_MS = 340;
  const FLIP_EASE = "cubic-bezier(.2,.7,.2,1)";

  function captureRects(container) {
    if (!container || state.reduced) return null;
    const map = new Map();
    for (const el of container.querySelectorAll(FLIP_SEL)) {
      const r = el.getBoundingClientRect();
      map.set(el.dataset.flipKey, { x: r.left, y: r.top });
    }
    return map;
  }

  function playFlip(container, prev) {
    if (!container || !prev || state.reduced) return;
    const els = container.querySelectorAll(FLIP_SEL);
    let entered = 0;
    els.forEach((el) => {
      // A second click interrupts the first instead of queueing behind it.
      for (const a of el.getAnimations()) if (a.id === "flip") a.cancel();
      const before = prev.get(el.dataset.flipKey);
      const r = el.getBoundingClientRect();
      if (!before) {
        const anim = el.animate(
          [{ opacity: 0, translate: "0 7px" }, { opacity: 1, translate: "0 0" }],
          { duration: FLIP_MS, delay: Math.min(entered++, 8) * 18, easing: FLIP_EASE, fill: "backwards" }
        );
        anim.id = "flip";
        return;
      }
      const dx = before.x - r.left, dy = before.y - r.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
      const anim = el.animate(
        [{ translate: dx.toFixed(1) + "px " + dy.toFixed(1) + "px" }, { translate: "0 0" }],
        { duration: FLIP_MS, easing: FLIP_EASE }
      );
      anim.id = "flip";
    });
  }

  /* ── 3. View Transitions for page switches ── */

  /* Falls back to a plain call everywhere it is not supported or not wanted,
     so the caller never has to branch. */
  function viewTransition(apply) {
    if (state.reduced || !document.startViewTransition) { apply(); return; }
    try {
      document.startViewTransition(apply);
    } catch (e) {
      apply();
    }
  }

  window.MOTION = {
    captureRects,
    playFlip,
    viewTransition,
    get reduced() { return state.reduced; },
    pointerFxEnabled: () => state.pointerFx,
    setPointerFx(on) {
      state.pointerFx = !!on;
      localStorage.setItem("ml.pointerfx", on ? "on" : "off");
      syncPointerGate();
    },
  };
})();
