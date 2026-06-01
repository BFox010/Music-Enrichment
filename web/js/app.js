import { api } from "./api.js";
import { filters } from "./filters.js";
import * as genreBubbles    from "./charts/genre_bubbles.js";
import * as artistTraj      from "./charts/artist_trajectory.js";
import * as timeMap         from "./charts/time_map.js";
import * as timeline        from "./charts/timeline.js";
import * as moods           from "./charts/moods.js";
import * as audioFeatures   from "./charts/audio_features.js";
import * as saturation      from "./charts/saturation.js";

// ── Nav ───────────────────────────────────────────────────────────────────────

const _initialized = new Set();

function showSection(id) {
  document.querySelectorAll(".section").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".nav-link").forEach(el => el.classList.remove("active"));

  const sec = document.getElementById(id);
  if (sec) sec.classList.add("active");

  const link = document.querySelector(`.nav-link[data-section="${id}"]`);
  if (link) link.classList.add("active");

  if (!_initialized.has(id)) {
    _initialized.add(id);
    initSection(id);
  }
}

function initSection(id) {
  switch (id) {
    case "overview":   loadOverview(); break;
    case "genres":     genreBubbles.init("genre-bubble-chart"); break;
    case "moods":      moods.init("moods-chart"); break;
    case "timeline":   timeline.init("timeline-chart"); break;
    case "listening":  timeMap.init("calendar-chart", "hw-chart"); break;
    case "artists":    artistTraj.init("artist-traj-chart"); loadTopArtists(); break;
    case "audio":      audioFeatures.init("scatter-chart", "hist-chart"); break;
    case "saturation": saturation.init("saturation-chart"); break;
    case "tracks":     loadTracks(); break;
  }
}

// ── Overview ──────────────────────────────────────────────────────────────────

async function loadOverview() {
  const data = await api.overview();
  document.getElementById("stat-tracks").textContent  = data.track_count.toLocaleString();
  document.getElementById("stat-plays").textContent   = data.scrobble_count.toLocaleString();
  const rng = data.scrobble_range;
  document.getElementById("stat-range").textContent   = rng.first ? `${rng.first} – ${rng.last}` : "—";

  const covList = document.getElementById("coverage-list");
  covList.innerHTML = "";
  for (const [label, val] of Object.entries(data.coverage)) {
    const pct = val.pct;
    covList.insertAdjacentHTML("beforeend", `
      <li class="cov-item">
        <span class="cov-label">${label.replace(/_/g, " ")}</span>
        <div class="cov-bar-wrap">
          <div class="cov-bar" style="width:${pct}%"></div>
        </div>
        <span class="cov-pct">${pct}%</span>
      </li>`);
  }

  // top tracks preview
  const top = await api.top("tracks", 10);
  const topList = document.getElementById("top-tracks-list");
  topList.innerHTML = top.map((t, i) =>
    `<li><span class="rank">${i + 1}</span><span class="tname">${t.artist} — ${t.track}</span><span class="plays">${t.plays}</span></li>`
  ).join("");
}

// ── Top artists ───────────────────────────────────────────────────────────────

async function loadTopArtists() {
  const data = await api.top("artists", 20);
  const list = document.getElementById("top-artists-list");
  if (!list) return;
  const max = data[0]?.plays || 1;
  list.innerHTML = data.map((a, i) => `
    <li class="top-item">
      <span class="rank">${i + 1}</span>
      <span class="aname">${a.name}</span>
      <div class="mini-bar-wrap"><div class="mini-bar" style="width:${Math.round(a.plays / max * 100)}%"></div></div>
      <span class="plays">${a.plays}</span>
    </li>`).join("");
}

// ── Timeline toggles ──────────────────────────────────────────────────────────

document.getElementById("btn-by-year")?.addEventListener("click", () => {
  setActive("btn-by-year", "btn-by-month");
  timeline.load("year");
});
document.getElementById("btn-by-month")?.addEventListener("click", () => {
  setActive("btn-by-month", "btn-by-year");
  timeline.load("month");
});

function setActive(on, off) {
  document.getElementById(on)?.classList.add("active");
  document.getElementById(off)?.classList.remove("active");
}

// ── Tracks table + filter bar ─────────────────────────────────────────────────

let _tracksPage = 1;

async function loadTracks(page = 1) {
  _tracksPage = page;
  const params = { ...filters.toParams(), page, per_page: 50 };
  const data = await api.tracks(params);
  renderTracks(data);
}

function renderTracks({ total, page, per_page, tracks }) {
  const tbody = document.getElementById("tracks-tbody");
  const info  = document.getElementById("tracks-info");
  if (!tbody) return;

  tbody.innerHTML = tracks.map(t => `
    <tr>
      <td>${esc(t.artist)}</td>
      <td>${esc(t.track)}</td>
      <td class="num">${t.play_count ?? ""}</td>
      <td>${(t.genres ?? []).join(", ")}</td>
      <td>${(t.mood_tags ?? []).join(", ")}</td>
      <td class="num">${t["audio_features.energy"] != null ? (+t["audio_features.energy"]).toFixed(2) : ""}</td>
      <td class="num">${t["audio_features.valence"] != null ? (+t["audio_features.valence"]).toFixed(2) : ""}</td>
      <td>${t.saturation_tier ?? ""}</td>
    </tr>`).join("");

  if (info) {
    const start = (page - 1) * per_page + 1;
    const end   = Math.min(page * per_page, total);
    info.textContent = total ? `Showing ${start}–${end} of ${total}` : "No tracks";
  }

  document.getElementById("btn-prev")?.toggleAttribute("disabled", page <= 1);
  document.getElementById("btn-next")?.toggleAttribute("disabled", page * per_page >= total);
}

document.getElementById("btn-prev")?.addEventListener("click", () => loadTracks(_tracksPage - 1));
document.getElementById("btn-next")?.addEventListener("click", () => loadTracks(_tracksPage + 1));

// Filter bar
["filter-genre", "filter-mood", "filter-artist"].forEach(id => {
  document.getElementById(id)?.addEventListener("input", e => {
    filters.set({ [id.replace("filter-", "")]: e.target.value });
  });
});
document.getElementById("filter-year")?.addEventListener("input", e => {
  filters.set({ year: e.target.value ? +e.target.value : null });
});
document.getElementById("filter-energy-min")?.addEventListener("input", e => {
  filters.set({ min_energy: e.target.value ? +e.target.value : null });
  document.getElementById("lbl-energy-min").textContent = e.target.value || "0";
});
document.getElementById("filter-energy-max")?.addEventListener("input", e => {
  filters.set({ max_energy: e.target.value ? +e.target.value : null });
  document.getElementById("lbl-energy-max").textContent = e.target.value || "1";
});
document.getElementById("btn-clear-filters")?.addEventListener("click", () => {
  filters.clear();
  document.querySelectorAll(".filter-bar input").forEach(el => el.value = "");
  document.getElementById("lbl-energy-min").textContent = "0";
  document.getElementById("lbl-energy-max").textContent = "1";
});

filters.onChange(() => {
  if (_initialized.has("tracks")) loadTracks(1);
});

// ── Reload button ─────────────────────────────────────────────────────────────

document.getElementById("btn-reload")?.addEventListener("click", async () => {
  const btn = document.getElementById("btn-reload");
  btn.disabled = true;
  btn.textContent = "Reloading…";
  try {
    const r = await api.reload();
    _initialized.clear();
    showSection(document.querySelector(".nav-link.active")?.dataset.section || "overview");
    btn.textContent = `↺ ${r.tracks} tracks`;
    setTimeout(() => { btn.textContent = "↺ Reload data"; btn.disabled = false; }, 2000);
  } catch {
    btn.textContent = "Error";
    btn.disabled = false;
  }
});

// ── Nav wiring ────────────────────────────────────────────────────────────────

document.querySelectorAll(".nav-link").forEach(el => {
  el.addEventListener("click", e => {
    e.preventDefault();
    showSection(el.dataset.section);
  });
});

function esc(str) {
  return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Boot
showSection("overview");
