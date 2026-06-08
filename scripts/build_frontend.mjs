// Frontend build: pre-compile the in-browser JSX into one minified bundle so the
// browser no longer ships @babel/standalone (~3 MB) or transpiles on every page load.
//
// Today the five .jsx files are run by @babel/standalone via indirect eval, which gives
// each file its OWN scope: top-level `function` declarations leak onto `window` (that is
// how components are shared, e.g. dashboard.jsx reads window.TimelineChart / bare HBars),
// while `const { useState } = React` stays private per file. To reproduce that exactly we
// wrap each file in its own IIFE and re-export its top-level functions to `window`, then
// run a single esbuild transform+minify pass over the combined source. The per-file IIFEs
// keep each file's scope isolated (no `const` collisions) and one pass yields one sourcemap.
//
//   npm run build     one-off build  -> web/app.bundle.js (+ .map)
//   npm run dev       rebuild on save (watch web/*.jsx)
import { transform } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";
import { watch } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "web");
// Order matters: components must be defined before dashboard.jsx consumes them.
const SOURCES = [
  "tweaks-panel.jsx",
  "charts.jsx",
  "explorer.jsx",
  "echarts-charts.jsx",
  "dashboard.jsx",
];
const OUT = join(WEB, "app.bundle.js");
const TOP_FN = /^function\s+([A-Za-z0-9_$]+)\s*\(/gm;

async function wrapFile(name) {
  const src = await readFile(join(WEB, name), "utf8");
  const names = [...src.matchAll(TOP_FN)].map((m) => m[1]);
  // Re-export every top-level function to window (mirrors indirect-eval's global leak).
  const exports = names.map((n) => `window.${n}=${n};`).join("");
  return `\n//# === ${name} ===\n(function(){\n${src}\n;${exports}\n})();\n`;
}

async function build() {
  const t0 = performance.now();
  const combined = (await Promise.all(SOURCES.map(wrapFile))).join("\n");
  const result = await transform(combined, {
    loader: "jsx",
    jsx: "transform", // classic runtime -> React.createElement (global React)
    jsxFactory: "React.createElement",
    jsxFragment: "React.Fragment",
    minify: true,
    sourcemap: true,
    sourcefile: "app.bundle.src.js",
    legalComments: "none",
  });
  await writeFile(OUT, result.code + "\n//# sourceMappingURL=app.bundle.js.map\n");
  await writeFile(OUT + ".map", result.map);
  const ms = (performance.now() - t0).toFixed(0);
  const kb = (Buffer.byteLength(result.code) / 1024).toFixed(1);
  console.log(`[build] web/app.bundle.js  ${kb} KB  (${ms} ms)`);
}

await build();

if (process.argv.includes("--watch")) {
  console.log("[watch] watching web/*.jsx — Ctrl+C to stop");
  let timer = null;
  for (const f of SOURCES) {
    watch(join(WEB, f), () => {
      clearTimeout(timer);
      timer = setTimeout(() => build().catch((e) => console.error("[build] error:", e.message)), 50);
    });
  }
}
