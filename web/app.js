/* KOKO dashboard: Leaflet map + click-for-report panel. Talks to the FastAPI backend on the same origin. */
"use strict";

const API = "";  // same origin as the FastAPI server
const TOKYO = [35.69, 139.77];
const BUILDINGS_MIN_ZOOM = 16;

// Official MLIT legend for flood depth (想定最大規模), so our colours match the GSI hazard tiles
const DEPTH_CLASSES = [
  { max: 0.5, color: "#F7F5A9", label: "< 0.5 m" },
  { max: 3, color: "#FFD8C0", label: "0.5 – 3 m" },
  { max: 5, color: "#FFB7B7", label: "3 – 5 m" },
  { max: 10, color: "#FF9191", label: "5 – 10 m" },
  { max: 20, color: "#F285C9", label: "10 – 20 m" },
  { max: Infinity, color: "#DC7ADC", label: "20 m +" },
];
const HAZARD_NAMES = {
  flood: "Flood", landslide: "Landslide", storm_surge: "Storm surge", earthquake: "Earthquake",
  tsunami: "Tsunami", fire: "Large fire", inland_flood: "Inland flood", volcano: "Volcano",
};
const LEVEL_TEXT = { none: "Not in zone", low: "Low", mid: "Moderate", high: "High" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtM = (v) => (v == null ? "?" : `${+v.toFixed(2)} m`);
const range = (r, unit) => (r.max == null ? `${r.min} ${unit} or more` : `${r.min} – ${r.max} ${unit}`);

function hours(r) {
  const h = (x) => (x >= 168 ? `${x / 168} wk` : x >= 24 ? `${x / 24} d` : `${x} h`);
  return r.max == null ? `${h(r.min)} or more` : `${h(r.min)} – ${h(r.max)}`;
}

// ---------- map & layers ----------
const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView(TOKYO, 12);

const gsiAttr = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>';
const base = {
  "GSI pale (淡色地図)": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", { maxNativeZoom: 18, maxZoom: 19, attribution: gsiAttr }),
  "GSI standard (標準地図)": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", { maxNativeZoom: 18, maxZoom: 19, attribution: gsiAttr }),
  "Aerial photo (写真)": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", { maxNativeZoom: 18, maxZoom: 19, attribution: gsiAttr }),
  "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
};
base["GSI pale (淡色地図)"].addTo(map);

const hazAttr = '<a href="https://disaportal.gsi.go.jp/hazardmapportal/hazardmap/copyright/opendata.html" target="_blank">ハザードマップポータルサイト</a>';
const hazardTile = (name, opacity = 0.65) =>
  L.tileLayer(`https://disaportaldata.gsi.go.jp/raster/${name}/{z}/{x}/{y}.png`, { opacity, maxNativeZoom: 17, maxZoom: 19, attribution: hazAttr });

const floodDepth = hazardTile("01_flood_l2_shinsuishin_data").addTo(map);
const overlays = {
  "Flood depth — max assumed": floodDepth,
  "Flood duration": hazardTile("01_flood_l2_keizoku_data"),
  "Storm surge depth": hazardTile("03_hightide_l2_shinsuishin_data"),
  "Landslide zones": L.layerGroup([
    hazardTile("05_kyukeishakeikaikuiki", 0.8), hazardTile("05_dosekiryukeikaikuiki", 0.8), hazardTile("05_jisuberikeikaikuiki", 0.8),
  ]),
};

// Ward boundaries (our PostGIS)
const wards = L.geoJSON(null, {
  style: { color: "#0b5cad", weight: 1.5, fill: false, opacity: 0.7 },
  onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.name_en} · ${f.properties.name_ja}`, { sticky: true }),
}).addTo(map);
fetch(`${API}/layers/wards`).then((r) => r.json()).then((d) => wards.addData(d)).catch(() => {});

// Evacuation sites (our PostGIS, from GSI 指定緊急避難場所)
const shelters = L.geoJSON(null, {
  pointToLayer: (f, ll) => L.circleMarker(ll, {
    radius: 4, weight: 1.5, color: "#ffffff", fillColor: f.properties.flood ? "#0b5cad" : "#5b6670", fillOpacity: 0.95,
  }),
  onEachFeature: (f, layer) => layer.bindPopup(shelterPopup(f.properties)),
});
fetch(`${API}/layers/shelters`).then((r) => r.json()).then((d) => shelters.addData(d)).catch(() => {});
overlays["Evacuation sites"] = shelters;
shelters.addTo(map);

// PLATEAU buildings, coloured by their own flood depth (our PostGIS); loaded per view when zoomed in
const buildings = L.geoJSON(null, {
  style: (f) => {
    const d = f.properties.flood_depth_m;
    return {
      weight: 0.6, color: "#555", fillOpacity: 0.85,
      fillColor: d == null || d <= 0 ? "#e9ecef" : depthClass(d).color,
    };
  },
  onEachFeature: (f, layer) => {
    const p = f.properties;
    layer.bindTooltip(`${esc(p.usage || "Building")} · ${p.storeys ?? "?"} floors<br>Flood depth here: ${p.flood_depth_m == null ? "none" : fmtM(p.flood_depth_m)}`, { sticky: true });
  },
});
overlays["PLATEAU buildings (zoom in)"] = buildings;
buildings.addTo(map);

let bldgReq = 0;
async function refreshBuildings() {
  if (!map.hasLayer(buildings)) return;
  buildings.clearLayers();
  if (map.getZoom() < BUILDINGS_MIN_ZOOM) return;
  const b = map.getBounds();
  const id = ++bldgReq;
  try {
    const r = await fetch(`${API}/layers/buildings?bbox=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",")}`);
    if (!r.ok || id !== bldgReq) return;
    buildings.addData(await r.json());
  } catch { /* offline: keep map usable */ }
}
map.on("moveend", refreshBuildings);
map.on("overlayadd", (e) => e.layer === buildings && refreshBuildings());

L.control.layers(base, overlays, { collapsed: window.innerWidth < 820 }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

// Legend
const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = "<b>Flood depth (max assumed)</b>" +
    DEPTH_CLASSES.map((c) => `<div><i style="background:${c.color}"></i>${c.label}</div>`).join("") +
    `<div class="note">Buildings: zoom in to ${BUILDINGS_MIN_ZOOM}+</div>`;
  return div;
};
legend.addTo(map);

function depthClass(d) { return DEPTH_CLASSES.find((c) => d < c.max); }

// ---------- report ----------
const panel = document.getElementById("panel");
let pin = null;

map.on("click", (e) => runReport(e.latlng.lat, e.latlng.lng));

async function runReport(lat, lon, label) {
  if (pin) pin.setLatLng([lat, lon]); else pin = L.marker([lat, lon]).addTo(map);
  panel.innerHTML = `<div class="loading">Checking ${lat.toFixed(5)}, ${lon.toFixed(5)}…</div>`;
  try {
    const [rep, addr] = await Promise.all([
      fetch(`${API}/report?lat=${lat}&lon=${lon}`).then(async (r) => {
        if (r.status === 422) throw new Error((await r.json()).detail);
        if (!r.ok) throw new Error(`Server error ${r.status}`);
        return r.json();
      }),
      label ? Promise.resolve(label) : reverseGeocode(lat, lon),
    ]);
    panel.innerHTML = renderReport(rep, addr);
    panel.scrollTop = 0;
    panel.querySelectorAll("[data-shelter]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const [la, lo] = btn.dataset.shelter.split(",").map(Number);
        map.flyTo([la, lo], 17);
        L.popup().setLatLng([la, lo]).setContent(esc(btn.textContent)).openOn(map);
      }));
  } catch (err) {
    panel.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}

async function reverseGeocode(lat, lon) {
  try {
    const r = await fetch(`https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat=${lat}&lon=${lon}`);
    const d = await r.json();
    return d.results?.lv01Nm || null;
  } catch { return null; }
}

function levels(rep) {
  const f = rep.flood, ss = rep.storm_surge, eq = rep.earthquake;
  const depthLevel = (rank) => (rank >= 3 ? "high" : rank === 2 ? "mid" : "low");
  const flood = !f.in_zone ? "none" : depthLevel(f.max_assumed?.rank ?? 1);
  const surge = !ss.in_zone ? "none" : (ss.max ?? 99) <= 0.5 ? "low" : (ss.max ?? 99) <= 3 ? "mid" : "high";
  const slide = rep.landslide.some((z) => z.zone?.startsWith("special")) ? "high" : rep.landslide.length ? "mid" : "none";
  // J-SHIS's own map classes for 30-year probability: 3% / 6% / 26%
  const p6 = eq?.probability_30y?.["6-"];
  const quake = p6 == null ? null : p6 >= 0.26 ? "high" : p6 >= 0.06 ? "mid" : "low";
  return { flood, surge, slide, quake };
}

function chip(name, lv, text) {
  return `<div class="chip lv-${lv}"><span class="dot" aria-hidden="true"></span>
    <div><b>${name}</b><span class="level">${text ?? LEVEL_TEXT[lv]}</span></div></div>`;
}

function renderReport(rep, addr) {
  const w = rep.location.ward;
  const lv = levels(rep);
  const p6 = rep.earthquake?.probability_30y?.["6-"];
  let html = `<div class="place"><h2>${esc(addr ? `${w.name_ja} ${addr}` : w.name_ja)}</h2>
    <p>${esc(w.name_en)} ward · ${rep.location.lat.toFixed(5)}, ${rep.location.lon.toFixed(5)}</p></div>`;

  html += `<div class="chips">
    ${chip("Flood", lv.flood)}
    ${chip("Storm surge", lv.surge)}
    ${chip("Landslide", lv.slide)}
    ${lv.quake ? chip("Earthquake (6-lower)", lv.quake, `${LEVEL_TEXT[lv.quake]} · ${Math.round(p6 * 100)}%`) : chip("Earthquake", "none", "Unavailable")}
  </div>`;

  html += floodCard(rep);
  html += buildingCard(rep);
  html += quakeCard(rep.earthquake);
  html += otherCard(rep);
  html += shelterCard(rep.shelters);
  html += `<p class="disclaimer">${esc(rep.disclaimer)}</p>`;
  return html;
}

function floodCard(rep) {
  const f = rep.flood;
  if (!f.in_zone) {
    return `<div class="card"><h3>🌊 River flood</h3>
      <p>Not inside a designated river-flood zone.</p>
      <p class="src">Not being in a zone is not zero risk: heavy rain can still flood streets (内水氾濫).</p></div>`;
  }
  const collapse = f.house_collapse_zone.flood_flow || f.house_collapse_zone.bank_erosion;
  return `<div class="card"><h3>🌊 River flood — worst-case scenario</h3>
    <div class="big">${range(f.max_assumed, "m")} deep</div>
    <dl>
      <dt>Water may stay</dt><dd>${f.duration ? hours(f.duration) : "not mapped"}</dd>
      <dt>Planned-scale flood</dt><dd>${f.planned_scale ? range(f.planned_scale, "m") : "not in zone"}</dd>
      <dt>House-collapse zone</dt><dd>${collapse ? "<b>Yes</b>: buildings may be swept away or undermined" : "No"}</dd>
    </dl>
    <p class="src">Source: 国土数値情報 洪水浸水想定区域 (A31b, 2025)</p></div>`;
}

function buildingCard(rep) {
  const b = rep.building, v = rep.vertical_evacuation;
  if (!b && !v) return "";
  let html = `<div class="card"><h3>🏢 This building</h3>`;
  if (b) {
    html += `<dl>
      <dt>Use</dt><dd>${esc(b.usage_label || "unknown")}</dd>
      <dt>Floors</dt><dd>${b.storeys_above ?? "?"} above ground${b.storeys_below ? `, ${b.storeys_below} below` : ""}</dd>
      <dt>Height</dt><dd>${b.height_m == null ? "?" : fmtM(b.height_m)}</dd>
      <dt>PLATEAU flood depth</dt><dd>${b.flood_max_depth_m == null ? "none" : fmtM(b.flood_max_depth_m)}</dd>
    </dl>`;
  } else {
    html += `<p>No PLATEAU building data at this point yet.</p>`;
  }
  if (v) {
    const cls = v.verdict === "leave_building" ? "high" : v.verdict === "unknown" ? "none" : v.isolation_24h_or_more ? "mid" : "low";
    const title = {
      leave_building: "Leave this building before the flood",
      upper_floors_ok: `Floor ${v.lowest_safe_floor} and above should stay dry`,
      minor: "Water is not expected above the ground floor",
      unknown: "Can't judge which floor is safe",
    }[v.verdict];
    html += `<div class="verdict lv-${cls}" style="margin-top:10px"><strong>${title}</strong>${esc(v.reason)}</div>`;
    html += `<p class="src">Assumes ${fmtM(v.expected_depth_m)} of water (${v.depth_basis === "plateau_building" ? "PLATEAU building value" : "cautious upper bound of the flood zone"})${v.storey_height_m ? `, about ${v.storey_height_m} m per floor, plus a ${v.safety_margin_m} m margin` : ""}.</p>`;
  }
  return html + `<p class="src">Source: Project PLATEAU 3D city model (CityGML 2025)</p></div>`;
}

function quakeCard(eq) {
  if (!eq || eq.error || !eq.probability_30y) {
    return `<div class="card"><h3>🫨 Earthquake</h3><p>J-SHIS is unavailable right now.</p></div>`;
  }
  const names = { "5-": "Shindo 5-", "5+": "Shindo 5+", "6-": "Shindo 6-", "6+": "Shindo 6+" };
  const bars = Object.entries(eq.probability_30y).map(([k, p]) =>
    `<div class="bar"><span class="lab">${names[k]}</span>
      <span class="track"><span class="fill" style="width:${(p * 100).toFixed(1)}%"></span></span>
      <span class="val">${(p * 100).toFixed(p < 0.1 ? 1 : 0)}%</span></div>`).join("");
  return `<div class="card"><h3>🫨 Chance of strong shaking in the next 30 years</h3>
    <div class="bars" role="list">${bars}</div>
    <p>At Shindo 6-lower, unsecured furniture falls and older wooden houses can be damaged.</p>
    <p class="src">Source: J-SHIS (NIED) probabilistic seismic hazard map, Y2024, 250 m mesh ${esc(eq.meshcode)}</p></div>`;
}

function otherCard(rep) {
  const ss = rep.storm_surge, ts = rep.tsunami;
  const slides = rep.landslide;
  const slideText = slides.length
    ? slides.map((z) => `${z.zone?.startsWith("special") ? "<b>Special warning zone (red)</b>" : "Warning zone (yellow)"}: ${esc(z.phenomenon?.replaceAll("_", " "))}${z.name ? ` · ${esc(z.name)}` : ""}`).join("<br>")
    : "Not in a designated zone";
  return `<div class="card"><h3>⚠️ Other hazards</h3><dl>
    <dt>Storm surge</dt><dd>${ss.in_zone ? `${esc(ss.depth_label)} (typhoon worst case)` : "Not in a designated zone"}</dd>
    <dt>Landslide</dt><dd>${slideText}</dd>
    <dt>Tsunami</dt><dd>${ts.in_zone ? esc(ts.depth_label) : "Not in a designated zone"}</dd>
  </dl>
  <p class="src">Sources: 国土数値情報 A49 storm surge (2020), A33 landslide (2023), A40 tsunami (2023)</p></div>`;
}

function shelterCard(s) {
  const tags = (list) => `<div class="tags">${Object.keys(HAZARD_NAMES).map((h) =>
    list.includes(h) ? `<span class="tag on">${HAZARD_NAMES[h]}</span>` : "").join("")}</div>`;
  const item = (x, prefix = "") => `<li>${prefix}<button data-shelter="${x.lat},${x.lon}">${esc(x.name)}</button>
    · ${x.distance_m.toLocaleString()} m<br><span class="src">${esc(x.address || "")}</span>
    ${x.remarks ? `<br><span class="src">${esc(x.remarks)}</span>` : ""}${tags(x.for)}</li>`;

  let html = `<div class="card"><h3>🏫 Evacuation sites</h3>`;
  const per = Object.entries(s.nearest_for_hazard).filter(([, x]) => x);
  if (per.length) {
    html += `<p>Nearest site rated for each danger here:</p><ul class="shelters">` +
      per.map(([h, x]) => item(x, `<b>${HAZARD_NAMES[h]}:</b> `)).join("") + `</ul>`;
  }
  html += `<p style="margin-top:10px">All nearby sites:</p><ul class="shelters">${s.nearest.map((x) => item(x)).join("")}</ul>
    <p class="src">Straight-line distance. Source: 国土地理院 指定緊急避難場所データ</p></div>`;
  return html;
}

function shelterPopup(p) {
  const types = Object.keys(HAZARD_NAMES).filter((h) => p[h]).map((h) => HAZARD_NAMES[h]).join(", ");
  return `<b>${esc(p.name)}</b><br>${esc(p.address || "")}<br><span style="color:#4a525c">For: ${esc(types || "—")}</span>` +
    (p.remarks ? `<br><small>${esc(p.remarks)}</small>` : "");
}

// ---------- search ----------
document.getElementById("search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = document.getElementById("q").value.trim();
  if (!q) return;
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m) return go(+m[1], +m[2]);
  panel.innerHTML = `<div class="loading">Searching “${esc(q)}”…</div>`;
  try {
    const r = await fetch(`https://msearch.gsi.go.jp/address-search/AddressSearch?q=${encodeURIComponent(q)}`);
    const hits = await r.json();
    if (!hits.length) throw new Error("Address not found. Try Japanese, e.g. 千代田区丸の内1丁目");
    const [lon, lat] = hits[0].geometry.coordinates;
    go(lat, lon, hits[0].properties.title);
  } catch (err) {
    panel.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
});

function go(lat, lon, label) {
  map.flyTo([lat, lon], 17);
  runReport(lat, lon, label);
}

document.querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  const [lat, lon] = a.dataset.goto.split(",").map(Number);
  go(lat, lon);
}));
