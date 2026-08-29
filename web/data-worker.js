/* data-worker.js — parses + aggregates the library off the main thread so the
   app shell stays responsive during a fresh load. Receives the raw JSONL text
   for tracks + scrobbles, runs the same processLibrary() the main thread uses,
   and posts back { nt, ns, drill, cube }. The main thread falls back to running
   processLibrary() inline if this worker is unavailable or errors. */
importScripts("data-processing.js");

self.onmessage = (e) => {
  // Either raw JSONL text (parsed here) or already-parsed rows — the
  // drag/drop path on the main thread can hand over pre-parsed rows since a
  // dropped file may be a plain JSON array rather than JSONL.
  const { tracksText, scrobblesText, trRows: preTr, scRows: preSc } = e.data || {};
  try {
    const trRows = preTr !== undefined ? preTr : (tracksText ? parseJSONL(tracksText) : null);
    const scRows = preSc !== undefined ? preSc : (scrobblesText ? parseJSONL(scrobblesText) : null);
    const { nt, ns, drill, cube } = processLibrary(trRows, scRows);
    // The cube's typed arrays are transferred, not cloned — moving ~130KB of
    // buffers costs nothing and keeps the hand-off off the copy path.
    const transfer = cube
      ? [cube.hour.buffer, cube.dow.buffer, cube.season.buffer, cube.tf.buffer, cube.track.buffer]
      : [];
    self.postMessage({ ok: true, nt, ns, drill, cube }, transfer);
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
