// Frontend build: compile web/*.jsx into one minified bundle (no in-browser transpile).
//
// The .jsx files share components through globals: every top-level `function` is
// visible to later files (e.g. dashboard.jsx reads window.TrajectoryPage and bare HBars),
// while each file's `const { useState } = React` must stay private. So each file is
// wrapped in its own IIFE that re-exports its top-level functions to `window`, then one
// esbuild transform+minify pass runs over the combined source (one sourcemap). Don't drop
// the IIFEs: the files' top-level `const`s would collide.
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
  // Re-export every top-level function to window so later files can use it.
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
