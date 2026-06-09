/* data-worker.js — parses + aggregates the library off the main thread so the
   app shell stays responsive during a fresh load. Receives the raw JSONL text
   for tracks + scrobbles, runs the same processLibrary() the main thread uses,
   and posts back { nt, ns, drill }. The main thread falls back to running
   processLibrary() inline if this worker is unavailable or errors. */
importScripts("data-processing.js");

self.onmessage = (e) => {
  const { tracksText, scrobblesText } = e.data || {};
  try {
    const trRows = tracksText ? parseJSONL(tracksText) : null;
    const scRows = scrobblesText ? parseJSONL(scrobblesText) : null;
    const { nt, ns, drill } = processLibrary(trRows, scRows);
    self.postMessage({ ok: true, nt, ns, drill });
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
