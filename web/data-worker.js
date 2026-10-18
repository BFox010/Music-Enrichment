/* Parses + aggregates the library off the main thread. Receives raw JSONL text
   or pre-parsed rows, runs the same processLibrary() the main thread uses, and
   posts back { ok, nt, ns, drill, cube, anchor }. The main thread falls back to
   running processLibrary() inline if this worker is unavailable or errors. */
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
    // The cube's typed arrays are transferred, not cloned.
    const transfer = cube
      ? [cube.hour.buffer, cube.dow.buffer, cube.season.buffer, cube.tf.buffer, cube.track.buffer]
      : [];
    // ANCHOR rides along because computeAnchor() set it in this realm only;
    // the main thread adopts it before rendering (#102).
    self.postMessage({ ok: true, nt, ns, drill, cube, anchor: ANCHOR }, transfer);
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};
