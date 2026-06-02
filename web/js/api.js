const BASE = "/api";

async function get(path, params = {}) {
  const url = new URL(BASE + path, location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== "") url.searchParams.set(k, v);
  }
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} (${url})`);
  return r.json();
}

export const api = {
  overview:          ()         => get("/overview"),
  genres:            (top = 50) => get("/genres", { top }),
  moods:             ()         => get("/moods"),
  timeline:          (by)       => get("/timeline", { by }),
  timeOfDay:         ()         => get("/time-of-day"),
  artistTrajectory:  (top = 15) => get("/artist-trajectory", { top }),
  top:               (dim, n)   => get("/top", { dim, n }),
  audioFeatures:     ()         => get("/audio-features"),
  saturation:        ()         => get("/saturation"),
  tracks:            (params)   => get("/tracks", params),
  tagGraph:          (field, minCount) => get("/tag-graph", { field, min_count: minCount }),
  reload:            ()         => fetch(BASE + "/reload", { method: "POST" }).then(r => r.json()),
};
