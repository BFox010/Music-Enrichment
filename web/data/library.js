// Placeholder — run scripts/generate_library_js.py to populate with your real library.
// When the FastAPI server is running (uvicorn app.main:app) this is replaced automatically
// by live data fetched from /tracks.jsonl and /scrobbles.jsonl.
window.MUSIC_DATA = {
  meta: { isSample: true, trackCount: 0, scrobbleCount: 0, scrobbleRange: "—" },
  tracks: [],
  scrobbles: { byHour: Array(24).fill(0), byDow: Array(7).fill(0), bySeason: {}, byYear: {}, total: 0 },
};
