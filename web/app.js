/* KOKO dashboard: Leaflet map + click-for-report panel. Talks to the FastAPI backend on the same origin.
   All visible text comes from i18n.js (t()), so the whole page switches between English and Japanese. */
"use strict";

const API = "";  // same origin as the FastAPI server
const JAPAN = [36.2, 138.3];
const BUILDINGS_MIN_ZOOM = 16;

// Official MLIT legend for flood depth (想定最大規模), so our colours match the GSI hazard tiles
const DEPTH_CLASSES = [
  { max: 0.5, color: "#F7F5A9", lo: 0, hi: 0.5 },
  { max: 3, color: "#FFD8C0", lo: 0.5, hi: 3 },
  { max: 5, color: "#FFB7B7", lo: 3, hi: 5 },
  { max: 10, color: "#FF9191", lo: 5, hi: 10 },
  { max: 20, color: "#F285C9", lo: 10, hi: 20 },
  { max: Infinity, color: "#DC7ADC", lo: 20, hi: null },
];
const HAZARDS = ["flood", "landslide", "storm_surge", "earthquake", "tsunami", "fire", "inland_flood", "volcano"];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const locale = () => (LANG === "ja" ? "ja-JP" : "en-US");
const num = (v) => (+v).toLocaleString(locale(), { maximumFractionDigits: 2 });
const fmtM = (v) => (v == null ? "?" : `${num(v)} ${t("unit.m")}`);
const range = (r, unit = t("unit.m")) => (r.max == null
  ? t("range.orMore", { a: num(r.min), u: unit })
  : t("range.between", { a: num(r.min), b: num(r.max), u: unit }));

function hours(r) {
  const h = (x) => (x >= 168 ? t("dur.w", { n: x / 168 }) : x >= 24 ? t("dur.d", { n: x / 24 }) : t("dur.h", { n: x }));
  return r.max == null ? `${h(r.min)}+` : `${h(r.min)} – ${h(r.max)}`;
}

// ---------- map & layers ----------
const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView(JAPAN, 6);

const gsiAttr = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>';
const baseLayers = {
  "map.base.pale": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", { maxNativeZoom: 18, maxZoom: 19, attribution: gsiAttr }),
  "map.base.std": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", { maxNativeZoom: 18, maxZoom: 19, attribution: gsiAttr }),
  "map.base.photo": L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", { maxNativeZoom: 18, maxZoom: 19, attribution: gsiAttr }),
  "map.base.osm": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
};
baseLayers["map.base.pale"].addTo(map);

const hazAttr = '<a href="https://disaportal.gsi.go.jp/hazardmapportal/hazardmap/copyright/opendata.html" target="_blank">ハザードマップポータルサイト</a>';
const hazardTile = (name, opacity = 0.65) =>
  L.tileLayer(`https://disaportaldata.gsi.go.jp/raster/${name}/{z}/{x}/{y}.png`, { opacity, maxNativeZoom: 17, maxZoom: 19, attribution: hazAttr });

const overlayLayers = {
  "map.layer.floodDepth": hazardTile("01_flood_l2_shinsuishin_data").addTo(map),
  "map.layer.floodDuration": hazardTile("01_flood_l2_keizoku_data"),
  "map.layer.surge": hazardTile("03_hightide_l2_shinsuishin_data"),
  "map.layer.landslide": L.layerGroup([
    hazardTile("05_kyukeishakeikaikuiki", 0.8), hazardTile("05_dosekiryukeikaikuiki", 0.8), hazardTile("05_jisuberikeikaikuiki", 0.8),
  ]),
};

// Layers from our PostGIS that are fetched for the current view only (there are too many for all of Japan)
function viewLayer(layer, path, minZoom) {
  let req = 0;
  async function refresh() {
    if (!map.hasLayer(layer)) return;
    if (map.getZoom() < minZoom) { layer.clearLayers(); return; }
    const b = map.getBounds();
    const id = ++req;
    try {
      const r = await fetch(`${API}${path}?bbox=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",")}`);
      if (!r.ok || id !== req) return;
      const data = await r.json();
      if (id !== req) return;
      layer.clearLayers();
      layer.addData(data);
    } catch { /* offline: keep map usable */ }
  }
  map.on("moveend", refresh);
  map.on("overlayadd", (e) => e.layer === layer && refresh());
  return refresh;
}

// Municipality boundaries (国土数値情報 N03). Tooltips are functions so they follow the current language.
const areas = L.geoJSON(null, {
  style: { color: "#0b5cad", weight: 1.2, fill: false, opacity: 0.6 },
  onEachFeature: (f, layer) => layer.bindTooltip(() => {
    const p = f.properties;
    return LANG === "ja" ? `${esc(p.pref_ja)} ${esc(p.name_ja ?? "")}` : `${esc(p.name_ja ?? "")} · ${esc(p.pref_en)}`;
  }, { sticky: true }),
});
overlayLayers["map.layer.areas"] = areas;
areas.addTo(map);
const refreshAreas = viewLayer(areas, "/layers/areas", 9);

// Evacuation sites (GSI 指定緊急避難場所)
const shelters = L.geoJSON(null, {
  pointToLayer: (f, ll) => L.circleMarker(ll, {
    radius: 4, weight: 1.5, color: "#ffffff", fillColor: f.properties.flood ? "#0b5cad" : "#5b6670", fillOpacity: 0.95,
  }),
  onEachFeature: (f, layer) => layer.bindPopup(() => shelterPopup(f.properties)),
});
overlayLayers["map.layer.shelters"] = shelters;
shelters.addTo(map);
const refreshShelters = viewLayer(shelters, "/layers/shelters", 12);

// PLATEAU buildings, coloured by their own flood depth (our PostGIS); loaded per view when zoomed in
const buildings = L.geoJSON(null, {
  style: (f) => {
    const d = f.properties.flood_depth_m;
    return { weight: 0.6, color: "#555", fillOpacity: 0.85, fillColor: d == null || d <= 0 ? "#e9ecef" : depthClass(d).color };
  },
  onEachFeature: (f, layer) => layer.bindTooltip(() => {
    const p = f.properties;
    return t("map.building.tip", {
      usage: esc(p.usage ? usageLabel(p.usage) : t("map.building")),
      floors: p.storeys ?? "?",
      depth: p.flood_depth_m == null ? t("bldg.none") : fmtM(p.flood_depth_m),
    });
  }, { sticky: true }),
});
overlayLayers["map.layer.buildings"] = buildings;
buildings.addTo(map);
const refreshBuildings = viewLayer(buildings, "/layers/buildings", BUILDINGS_MIN_ZOOM);
map.whenReady(() => { refreshAreas(); refreshShelters(); refreshBuildings(); });

L.control.scale({ imperial: false }).addTo(map);

// Layer switcher and legend are rebuilt when the language changes
let layerControl = null;
const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = `<b>${t("map.legend.title")}</b>` +
    DEPTH_CLASSES.map((c) => `<div><i style="background:${c.color}"></i>${c.lo === 0 ? `&lt; ${num(c.hi)} ${t("unit.m")}` : range({ min: c.lo, max: c.hi })}</div>`).join("") +
    `<div class="note">${t("map.legend.buildings", { z: BUILDINGS_MIN_ZOOM })}</div>`;
  return div;
};

function buildMapControls() {
  if (layerControl) layerControl.remove();
  const named = (obj) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [t(k), v]));
  layerControl = L.control.layers(named(baseLayers), named(overlayLayers), { collapsed: window.innerWidth < 820 }).addTo(map);
  legend.remove();
  legend.addTo(map);
}
buildMapControls();

function depthClass(d) { return DEPTH_CLASSES.find((c) => d < c.max); }

// ---------- report ----------
const panel = document.getElementById("panel");
let pin = null;
let selection = null;   // last place picked on the map; Ask KOKO uses it for "this place"
let lastReport = null;  // { rep, addr } so a language switch can re-render without refetching

map.on("click", (e) => runReport(e.latlng.lat, e.latlng.lng));

class ReportError extends Error {}

async function runReport(lat, lon, label) {
  if (pin) pin.setLatLng([lat, lon]); else pin = L.marker([lat, lon]).addTo(map);
  selection = { lat, lon, label: label || null };
  lastReport = null;
  panel.innerHTML = `<div class="loading">${t("report.checking", { lat: lat.toFixed(5), lon: lon.toFixed(5) })}</div>`;
  try {
    const [rep, addr] = await Promise.all([
      fetch(`${API}/report?lat=${lat}&lon=${lon}`).then(async (r) => {
        if (r.status === 422) throw new ReportError("report.outside");
        if (!r.ok) throw new Error(t("report.serverError", { status: r.status }));
        return r.json();
      }),
      label ? Promise.resolve(label) : reverseGeocode(lat, lon),
    ]);
    const a = rep.location.area;
    selection.label = addr && addr.startsWith(a.pref_ja) ? addr : `${a.pref_ja}${a.name_ja ?? ""}${addr ? ` ${addr}` : ""}`;
    lastReport = { rep, addr };
    showReport();
  } catch (err) {
    panel.innerHTML = `<div class="error">${esc(err instanceof ReportError ? t(err.message) : err.message)}</div>`;
  }
}

function showReport() {
  const { rep, addr } = lastReport;
  panel.innerHTML = renderReport(rep, addr);
  panel.scrollTop = 0;
  panel.querySelectorAll("[data-shelter]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const [la, lo] = btn.dataset.shelter.split(",").map(Number);
      map.flyTo([la, lo], 17);
      L.popup().setLatLng([la, lo]).setContent(esc(btn.textContent)).openOn(map);
    }));
}

async function reverseGeocode(lat, lon) {
  try {
    const r = await fetch(`https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat=${lat}&lon=${lon}`);
    const d = await r.json();
    return d.results?.lv01Nm || null;
  } catch { return null; }
}

function levels(rep) {
  const f = rep.flood, ss = rep.storm_surge, eq = rep.earthquake, has = rep.data_available;
  const depthLevel = (rank) => (rank >= 3 ? "high" : rank === 2 ? "mid" : "low");
  // "nodata" is not "none": no published dataset for this area is different from "outside every zone"
  const flood = f.in_zone ? depthLevel(f.max_assumed?.rank ?? 1) : has.flood ? "none" : "nodata";
  const surge = ss.in_zone ? ((ss.max ?? 99) <= 0.5 ? "low" : (ss.max ?? 99) <= 3 ? "mid" : "high")
    : has.storm_surge ? "none" : "nodata";
  const slide = rep.landslide.some((z) => z.zone?.startsWith("special")) ? "high" : rep.landslide.length ? "mid"
    : has.landslide ? "none" : "nodata";
  // J-SHIS's own map classes for 30-year probability: 3% / 6% / 26%
  const p6 = eq?.probability_30y?.["6-"];
  const quake = p6 == null ? null : p6 >= 0.26 ? "high" : p6 >= 0.06 ? "mid" : "low";
  return { flood, surge, slide, quake };
}

function chip(nameKey, lv, text) {
  return `<div class="chip lv-${lv}"><span class="dot" aria-hidden="true"></span>
    <div><b>${t(nameKey)}</b><span class="level">${text ?? t(`level.${lv}`)}</span></div></div>`;
}

function renderReport(rep, addr) {
  const a = rep.location.area;
  const lv = levels(rep);
  const p6 = rep.earthquake?.probability_30y?.["6-"];
  const place = addr && addr.startsWith(a.pref_ja) ? addr : `${a.pref_ja} ${a.name_ja ?? ""}${addr ? ` ${addr}` : ""}`;
  const sub = LANG === "ja" ? a.pref_ja : a.pref_en;
  let html = `<div class="place"><h2>${esc(place)}</h2>
    <p>${esc(sub)} · ${rep.location.lat.toFixed(5)}, ${rep.location.lon.toFixed(5)}</p></div>`;

  html += `<div class="chips">
    ${chip("chip.flood", lv.flood)}
    ${chip("chip.surge", lv.surge)}
    ${chip("chip.landslide", lv.slide)}
    ${lv.quake ? chip("chip.quake", lv.quake, `${t(`level.${lv.quake}`)} · ${p6 < 0.01 ? "<1" : Math.round(p6 * 100)}%`)
      : chip("chip.quakeShort", "none", t("level.unavailable"))}
  </div>`;

  const prefName = LANG === "ja" ? a.pref_ja : a.pref_en;
  const missing = Object.entries(rep.data_available).filter(([, ok]) => !ok).map(([k]) => t(`dataset.${k}`));
  if (missing.length === Object.keys(rep.data_available).length) {
    html += `<div class="card notice"><h3>⏳ ${esc(t("notice.loadingTitle", { pref: prefName }))}</h3>
      <p>${esc(t("notice.loadingBody", { pref: prefName }))}</p></div>`;
  } else if (missing.length) {
    html += `<div class="card notice"><p>${t("notice.partial", { list: esc(missing.join(t("list.sep"))) })}</p></div>`;
  }
  html += floodCard(rep);
  html += buildingCard(rep);
  html += quakeCard(rep.earthquake);
  html += otherCard(rep);
  html += shelterCard(rep.shelters);
  html += `<p class="disclaimer">${t("disclaimer")}</p>`;
  return html;
}

function floodCard(rep) {
  const f = rep.flood;
  if (!f.in_zone && !rep.data_available.flood) {
    return `<div class="card"><h3>🌊 ${t("flood.title")}</h3><p>${t("flood.notLoaded")}</p></div>`;
  }
  if (!f.in_zone) {
    return `<div class="card"><h3>🌊 ${t("flood.title")}</h3>
      <p>${t("flood.notInZone")}</p><p class="src">${t("flood.notZeroRisk")}</p></div>`;
  }
  const collapse = f.house_collapse_zone.flood_flow || f.house_collapse_zone.bank_erosion;
  return `<div class="card"><h3>🌊 ${t("flood.titleZone")}</h3>
    <div class="big">${t("flood.deep", { range: range(f.max_assumed) })}</div>
    <dl>
      <dt>${t("flood.stay")}</dt><dd>${f.duration ? hours(f.duration) : t("flood.notMapped")}</dd>
      <dt>${t("flood.planned")}</dt><dd>${f.planned_scale ? range(f.planned_scale) : t("flood.notInZoneShort")}</dd>
      <dt>${t("flood.collapse")}</dt><dd>${collapse ? t("flood.collapseYes") : t("flood.no")}</dd>
    </dl>
    <p class="src">${t("flood.source")}</p></div>`;
}

/* The verdict text is built here from the structured fields, so it follows the UI language. */
function verdictReason(v) {
  if (v.verdict === "leave_building" && v.house_collapse_zone) return t("bldg.r.collapse");
  if (v.verdict === "unknown") return t("bldg.r.noStoreys");
  if (v.verdict === "leave_building") return t("bldg.r.noFloor");
  let s = t("bldg.r.upper", { floor: v.lowest_safe_floor });
  if (v.isolation_24h_or_more) s += ` ${t("bldg.r.isolation")}`;
  return s;
}

function buildingCard(rep) {
  const b = rep.building, v = rep.vertical_evacuation;
  if (!b && !v) return "";
  let html = `<div class="card"><h3>🏢 ${t("bldg.title")}</h3>`;
  if (b) {
    html += `<dl>
      <dt>${t("bldg.use")}</dt><dd>${esc(usageLabel(b.usage_label))}</dd>
      <dt>${t("bldg.floors")}</dt><dd>${t("bldg.floorsValue", { above: b.storeys_above ?? "?" })}${b.storeys_below ? t("bldg.floorsBelow", { below: b.storeys_below }) : ""}</dd>
      <dt>${t("bldg.height")}</dt><dd>${fmtM(b.height_m)}</dd>
      <dt>${t("bldg.plateauDepth")}</dt><dd>${b.flood_max_depth_m == null ? t("bldg.none") : fmtM(b.flood_max_depth_m)}</dd>
    </dl>`;
  } else {
    html += `<p>${t("bldg.noData")}</p>`;
  }
  if (v) {
    const cls = v.verdict === "leave_building" ? "high" : v.verdict === "unknown" ? "none" : v.isolation_24h_or_more ? "mid" : "low";
    const title = {
      leave_building: t("bldg.v.leave"),
      upper_floors_ok: t("bldg.v.upper", { floor: v.lowest_safe_floor }),
      minor: t("bldg.v.minor"),
      unknown: t("bldg.v.unknown"),
    }[v.verdict];
    html += `<div class="verdict lv-${cls}" style="margin-top:10px"><strong>${title}</strong>${esc(verdictReason(v))}</div>`;
    const basis = t(v.depth_basis === "plateau_building" ? "bldg.basis.plateau" : "bldg.basis.zone");
    html += `<p class="src">${t("bldg.assumes", { depth: fmtM(v.expected_depth_m), basis })}${v.storey_height_m
      ? t("bldg.assumesFloors", { h: num(v.storey_height_m), m: num(v.safety_margin_m) }) : ""}${t("punct.end")}</p>`;
  }
  return html + `<p class="src">${t("bldg.source")}</p></div>`;
}

function quakeCard(eq) {
  if (!eq || eq.error || !eq.probability_30y) {
    return `<div class="card"><h3>🫨 ${t("quake.title")}</h3><p>${t("quake.unavailable")}</p></div>`;
  }
  const bars = Object.entries(eq.probability_30y).map(([k, p]) =>
    `<div class="bar"><span class="lab">${t(`quake.i.${k}`)}</span>
      <span class="track"><span class="fill" style="width:${(p * 100).toFixed(1)}%"></span></span>
      <span class="val">${(p * 100).toFixed(p < 0.1 ? 1 : 0)}%</span></div>`).join("");
  return `<div class="card"><h3>🫨 ${t("quake.title30")}</h3>
    <div class="bars" role="list">${bars}</div>
    <p>${t("quake.explain")}</p>
    <p class="src">${t("quake.source", { mesh: esc(eq.meshcode) })}</p></div>`;
}

function depthZone(z) {
  return z.min != null ? range({ min: z.min, max: z.max }) : esc(z.depth_label);
}

function otherCard(rep) {
  const ss = rep.storm_surge, ts = rep.tsunami, has = rep.data_available;
  const notIn = (key) => t(has[key] ? "other.notInZone" : "other.noData");
  const slides = rep.landslide;
  const slideText = slides.length
    ? slides.map((z) => `${t(`slide.zone.${z.zone}`)}${t("punct.colon")}${t(`slide.ph.${z.phenomenon}`)}${z.name ? ` · ${esc(z.name)}` : ""}`).join("<br>")
    : notIn("landslide");
  return `<div class="card"><h3>⚠️ ${t("other.title")}</h3><dl>
    <dt>${t("other.surge")}</dt><dd>${ss.in_zone ? t("other.typhoon", { range: depthZone(ss) }) : notIn("storm_surge")}</dd>
    <dt>${t("other.landslide")}</dt><dd>${slideText}</dd>
    <dt>${t("other.tsunami")}</dt><dd>${ts.in_zone ? depthZone(ts) : notIn("tsunami")}</dd>
  </dl>
  <p class="src">${t("other.source")}</p></div>`;
}

function shelterCard(s) {
  const tags = (list) => `<div class="tags">${HAZARDS.map((h) =>
    list.includes(h) ? `<span class="tag on">${t(`hazard.${h}`)}</span>` : "").join("")}</div>`;
  const item = (x, prefix = "") => `<li>${prefix}<button data-shelter="${x.lat},${x.lon}">${esc(x.name)}</button>
    · ${x.distance_m.toLocaleString(locale())} m<br><span class="src">${esc(x.address || "")}</span>
    ${x.remarks ? `<br><span class="src">${esc(x.remarks)}</span>` : ""}${tags(x.for)}</li>`;

  let html = `<div class="card"><h3>🏫 ${t("shelter.title")}</h3>`;
  const per = Object.entries(s.nearest_for_hazard).filter(([, x]) => x);
  if (per.length) {
    html += `<p>${t("shelter.perHazard")}</p><ul class="shelters">` +
      per.map(([h, x]) => item(x, `<b>${t(`hazard.${h}`)}${t("punct.colon")}</b>`)).join("") + `</ul>`;
  }
  if (!s.nearest.length) return html + `<p>${t("shelter.none")}</p></div>`;
  html += `<p style="margin-top:10px">${t("shelter.all")}</p><ul class="shelters">${s.nearest.map((x) => item(x)).join("")}</ul>
    <p class="src">${t("shelter.source")}</p></div>`;
  return html;
}

function shelterPopup(p) {
  const types = HAZARDS.filter((h) => p[h]).map((h) => t(`hazard.${h}`)).join(t("list.sep"));
  return `<b>${esc(p.name)}</b><br>${esc(p.address || "")}<br><span style="color:#4a525c">${t("map.shelter.for")}${t("punct.colon")}${esc(types || "—")}</span>` +
    (p.remarks ? `<br><small>${esc(p.remarks)}</small>` : "");
}

// ---------- search ----------
document.getElementById("search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = document.getElementById("q").value.trim();
  if (!q) return;
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m) return go(+m[1], +m[2]);
  panel.innerHTML = `<div class="loading">${esc(t("search.searching", { q }))}</div>`;
  try {
    const r = await fetch(`https://msearch.gsi.go.jp/address-search/AddressSearch?q=${encodeURIComponent(q)}`);
    const hits = await r.json();
    if (!hits.length) throw new Error(t("search.notFound"));
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

// ---------- language switch ----------
document.addEventListener("langchange", () => {
  buildMapControls();
  if (lastReport) showReport();
  map.closePopup();
});
