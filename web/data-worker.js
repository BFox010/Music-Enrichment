/* data-worker.js — off-main-thread work for the dashboard. Two jobs, chosen by
   the message's `kind`:

   - default: parses + aggregates the library so the app shell stays responsive
     during a fresh load. Receives the raw JSONL text for tracks + scrobbles,
     runs the same processLibrary() the main thread uses, and posts back
     { nt, ns, drill, cube }.
   - "graph-layout": settles a tag graph for the Tag Constellation and posts
     back the node positions.

   Both import the same plain-global source files the page loads, so the worker
   and the main thread run identical code; the main thread falls back to
   running either one inline if this worker is unavailable or errors. */
importScripts("data-processing.js", "graph-layout.js");

self.onmessage = (e) => {
  const msg = e.data || {};
  try {
    if (msg.kind === "graph-layout") {
      // Maps do survive structured cloning, but the entry array is cheaper and
      // says plainly what crosses the boundary.
      const positions = settleLayout(msg.nodes, msg.edges, msg.opts);
      self.postMessage({ ok: true, kind: "graph-layout", positions: [...positions] });
      return;
    }
    // Either raw JSONL text (parsed here) or already-parsed rows — the
    // drag/drop path on the main thread can hand over pre-parsed rows since a
    // dropped file may be a plain JSON array rather than JSONL.
    const { tracksText, scrobblesText, trRows: preTr, scRows: preSc } = msg;
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
    self.postMessage({ ok: false, kind: msg.kind, error: String(err && err.message || err) });
  }
};
