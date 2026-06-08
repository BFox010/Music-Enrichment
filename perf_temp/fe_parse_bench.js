const fs = require('fs');
function now(){ return Number(process.hrtime.bigint())/1e6; }

// 1) Raw file read
let t=now();
const tracksRaw = fs.readFileSync('tracks.jsonl','utf8');
const scrobRaw  = fs.readFileSync('scrobbles.jsonl','utf8');
const readMs = now()-t;

// 2) JSONL parse (mirrors web parseJSONL: split on \n, JSON.parse each line)
function parseJSONL(s){ const out=[]; for(const line of s.split('\n')){ if(line.trim()) out.push(JSON.parse(line)); } return out; }
t=now();
const tracks = parseJSONL(tracksRaw);
const scrobbles = parseJSONL(scrobRaw);
const parseMs = now()-t;

// 3) Representative client aggregation: buildPlayWindows-like (index scrobbles by year/month)
//    + buildDrill-like cross-tab (season/hour x genre). O(scrobbles) + O(tracks*genres).
t=now();
const byKey = new Map();
for(const s of scrobbles){
  const ts = s.timestamp || s.uts || s.date || 0;
  const d = new Date((typeof ts==='number'? ts*1000 : Date.parse(ts))||0);
  const key = d.getUTCFullYear()+'-'+d.getUTCMonth();
  byKey.set(key,(byKey.get(key)||0)+1);
}
// drill: for each track, fan out over its genres + moods into buckets
const drill = new Map();
for(const tr of tracks){
  const pc = tr.play_count||0;
  const tags = [].concat(tr.genres||[], tr.mood_tags||[], tr.discogs_styles||[]);
  for(const g of tags){ drill.set(g,(drill.get(g)||0)+pc); }
}
const aggMs = now()-t;

const heap = process.memoryUsage().heapUsed/1e6;
console.log(`tracks=${tracks.length} scrobbles=${scrobbles.length}`);
console.log(`file read       ms = ${readMs.toFixed(1)}`);
console.log(`JSONL parse     ms = ${parseMs.toFixed(1)}`);
console.log(`agg (windows+drill) ms = ${aggMs.toFixed(1)}  (buckets: ${byKey.size} periods, ${drill.size} tags)`);
console.log(`heapUsed after  MB = ${heap.toFixed(1)}`);
console.log(`on-wire bytes: tracks=${tracksRaw.length} scrobbles=${scrobRaw.length} total=${tracksRaw.length+scrobRaw.length}`);
