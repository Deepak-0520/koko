/* KOKO explorer: a MapLibre globe with Japan → prefecture → municipality drill-down.
   Hover highlights an area, click (or the dropdowns) zooms into it. Only the selected municipality's
   layers (PLATEAU 3D buildings, hazard zones, shelters) and statistics are loaded.
   All visible text comes from i18n.js (t()). Point reports are rendered by report_view.js. */
"use strict";

const ORIGIN = location.origin;
const GLOBE_VIEW = { center: [138, 30], zoom: 1.4 };
const JAPAN_BOUNDS = [[122.9, 24.0], [146.2, 45.6]];  // main islands to Okinawa
const BUILDINGS_MIN_ZOOM = 14;  // zoom-13 tiles in dense wards are ~1 MB

// Official MLIT legend for flood depth (想定最大規模), so colours match the national hazard maps
const DEPTH_CLASSES = [
  { max: 0.5, color: "#F7F5A9", lo: 0, hi: 0.5 },
  { max: 3, color: "#FFD8C0", lo: 0.5, hi: 3 },
  { max: 5, color: "#FFB7B7", lo: 3, hi: 5 },
  { max: 10, color: "#FF9191", lo: 5, hi: 10 },
  { max: 20, color: "#F285C9", lo: 10, hi: 20 },
  { max: Infinity, color: "#DC7ADC", lo: 20, hi: null },
];
const HAZARDS = ["flood", "landslide", "storm_surge", "earthquake", "tsunami", "fire", "inland_flood", "volcano"];
const COLORS = { accent: "#0b5cad", surge: "#3f7fbf", tsunami: "#0f9d9a", slide: "#f2c200", slideRed: "#d7263d", water: "#2f7fd8", noFlood: "#e4e7eb" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const locale = () => (LANG === "ja" ? "ja-JP" : "en-US");
const num = (v) => (+v).toLocaleString(locale(), { maximumFractionDigits: 2 });
const int = (v) => (+v || 0).toLocaleString(locale());
const pct = (f) => `${(f * 100).toFixed(f > 0 && f < 0.01 ? 1 : 0)}%`;
const fmtM = (v) => (v == null ? "?" : `${num(v)} ${t("unit.m")}`);
const range = (r, unit = t("unit.m")) => (r.max == null
  ? t("range.orMore", { a: num(r.min), u: unit })
  : t("range.between", { a: num(r.min), b: num(r.max), u: unit }));

function hours(r) {
  const h = (x) => (x >= 168 ? t("dur.w", { n: x / 168 }) : x >= 24 ? t("dur.d", { n: x / 24 }) : t("dur.h", { n: x }));
  return r.max == null ? `${h(r.min)}+` : `${h(r.min)} – ${h(r.max)}`;
}

async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(t("report.serverError", { status: r.status }));
  return r.json();
}

// ---------- state ----------
const state = {
  level: "japan",     // japan | pref | muni
  prefs: null,        // FeatureCollection of prefectures
  pref: null,         // selected prefecture properties
  munis: null,        // FeatureCollection of the selected prefecture's municipalities
  muni: null,         // selected municipality (from /areas/municipalities/{code})
  view: "area",       // area summary or point report in the side panel
};
let selection = null;   // what Ask KOKO calls "this place" (point or area)
let lastReport = null;  // { rep, addr } for re-rendering after a language switch
let lastStats = null;   // { kind, data } for re-rendering after a language switch
let pointMarker = null;

// ---------- map ----------
const empty = () => ({ type: "FeatureCollection", features: [] });
const map = new maplibregl.Map({
  container: "map",
  ...GLOBE_VIEW,
  maxPitch: 75,
  attributionControl: { compact: true },
  style: {
    version: 8,
    projection: { type: "globe" },
    sources: {
      pale: {
        type: "raster", tileSize: 256, maxzoom: 18,
        tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
        attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>',
      },
      photo: {
        type: "raster", tileSize: 256, maxzoom: 18,
        tiles: ["https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"],
        attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>',
      },
      prefs: { type: "geojson", data: empty(), promoteId: "code" },
      munis: { type: "geojson", data: empty(), promoteId: "code" },
      mask: { type: "geojson", data: empty() },
      shelters: { type: "geojson", data: empty() },
    },
    layers: [
      { id: "space", type: "background", paint: { "background-color": "#dbe7f3" } },
      { id: "pale", type: "raster", source: "pale" },
      { id: "photo", type: "raster", source: "photo", layout: { visibility: "none" } },
      // hazard layers of the selected municipality are inserted here (before "mask")
      { id: "mask", type: "fill", source: "mask", paint: { "fill-color": "#f6f7f9", "fill-opacity": 0.72 } },
      {
        id: "prefs-fill", type: "fill", source: "prefs",
        paint: {
          "fill-color": COLORS.accent,
          "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0,
            ["boolean", ["feature-state", "hover"], false], 0.28, 0.06],
        },
      },
      { id: "prefs-line", type: "line", source: "prefs", paint: { "line-color": COLORS.accent, "line-width": 1, "line-opacity": 0.7 } },
      {
        id: "munis-fill", type: "fill", source: "munis",
        paint: {
          "fill-color": COLORS.accent,
          "fill-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0,
            ["boolean", ["feature-state", "hover"], false], 0.25, 0.04],
        },
      },
      { id: "munis-line", type: "line", source: "munis", paint: { "line-color": COLORS.accent, "line-width": 0.8, "line-opacity": 0.6 } },
      {
        id: "muni-selected", type: "line", source: "munis", filter: ["==", ["get", "code"], ""],
        paint: { "line-color": COLORS.accent, "line-width": 3 },
      },
      // 3D buildings are added above here when a municipality is selected
      {
        id: "shelters", type: "circle", source: "shelters",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 16, 7],
          "circle-color": ["case", ["get", "flood"], COLORS.accent, "#5b6670"],
          "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5,
        },
      },
    ],
  },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

// ---------- per-municipality layers (vector tiles filtered to one municipality) ----------
const AREA_LAYERS = {
  flood: { visible: true, ids: ["flood"] },
  storm_surge: { visible: false, ids: ["storm_surge"] },
  tsunami: { visible: false, ids: ["tsunami"] },
  landslide: { visible: true, ids: ["landslide"] },
  buildings: { visible: true, ids: ["water", "bldg"] },
  shelters: { visible: true, ids: ["shelters"] },
};
const DEPTH = ["coalesce", ["get", "flood_depth_m"], 0];
const depthColor = ["case", ["<=", DEPTH, 0], COLORS.noFlood,
  ["step", DEPTH, DEPTH_CLASSES[0].color, 0.5, DEPTH_CLASSES[1].color, 3, DEPTH_CLASSES[2].color,
    5, DEPTH_CLASSES[3].color, 10, DEPTH_CLASSES[4].color, 20, DEPTH_CLASSES[5].color]];

function tileSource(layer, code, minzoom, maxzoom) {
  return { type: "vector", tiles: [`${ORIGIN}/tiles/${layer}/{z}/{x}/{y}.pbf?muni=${code}`], minzoom, maxzoom };
}

function removeAreaLayers() {
  for (const id of ["flood", "storm_surge", "tsunami", "landslide", "water", "bldg"]) if (map.getLayer(id)) map.removeLayer(id);
  for (const id of ["t-flood", "t-storm_surge", "t-tsunami", "t-landslide", "t-buildings"]) if (map.getSource(id)) map.removeSource(id);
  map.getSource("shelters").setData(empty());
}

function addAreaLayers(code) {
  removeAreaLayers();
  const vis = (key) => ({ visibility: AREA_LAYERS[key].visible ? "visible" : "none" });
  map.addSource("t-flood", tileSource("flood", code, 8, 15));
  map.addSource("t-storm_surge", tileSource("storm_surge", code, 8, 15));
  map.addSource("t-tsunami", tileSource("tsunami", code, 8, 15));
  map.addSource("t-landslide", tileSource("landslide", code, 8, 15));
  map.addSource("t-buildings", tileSource("buildings", code, BUILDINGS_MIN_ZOOM, 16));
  map.addLayer({
    id: "flood", type: "fill", source: "t-flood", "source-layer": "flood", layout: vis("flood"),
    paint: {
      "fill-color": ["match", ["get", "depth_rank"], 1, DEPTH_CLASSES[0].color, 2, DEPTH_CLASSES[1].color,
        3, DEPTH_CLASSES[2].color, 4, DEPTH_CLASSES[3].color, 5, DEPTH_CLASSES[4].color, DEPTH_CLASSES[5].color],
      "fill-opacity": 0.75,
    },
  }, "mask");
  map.addLayer({ id: "storm_surge", type: "fill", source: "t-storm_surge", "source-layer": "storm_surge", layout: vis("storm_surge"),
    paint: { "fill-color": COLORS.surge, "fill-opacity": 0.35 } }, "mask");
  map.addLayer({ id: "tsunami", type: "fill", source: "t-tsunami", "source-layer": "tsunami", layout: vis("tsunami"),
    paint: { "fill-color": COLORS.tsunami, "fill-opacity": 0.35 } }, "mask");
  map.addLayer({ id: "landslide", type: "fill", source: "t-landslide", "source-layer": "landslide", layout: vis("landslide"),
    paint: { "fill-color": ["match", ["get", "zone"], [2, 4], COLORS.slideRed, COLORS.slide], "fill-opacity": 0.55 } }, "mask");
  // buildings: the part below the expected flood depth is blue (the water line), the rest coloured by depth
  map.addLayer({ id: "water", type: "fill-extrusion", source: "t-buildings", "source-layer": "buildings", layout: vis("buildings"),
    filter: [">", DEPTH, 0],
    paint: { "fill-extrusion-color": COLORS.water, "fill-extrusion-height": DEPTH, "fill-extrusion-opacity": 1 } }, "shelters");
  map.addLayer({ id: "bldg", type: "fill-extrusion", source: "t-buildings", "source-layer": "buildings", layout: vis("buildings"),
    paint: {
      "fill-extrusion-color": depthColor,
      "fill-extrusion-base": ["min", DEPTH, ["get", "h"]],
      "fill-extrusion-height": ["get", "h"],
      "fill-extrusion-opacity": 1,
    } }, "shelters");
  getJSON(`/areas/municipalities/${code}/shelters`).then((fc) => {
    if (state.muni?.code === code) map.getSource("shelters").setData(fc);
  }).catch(() => {});
}

// ---------- hover ----------
const tip = document.getElementById("hover-tip");
let hovered = null;  // { source, id }

function setFeature(source, id, key, value) {
  if (id != null) map.setFeatureState({ source, id }, { [key]: value });
}

function hoverTarget(point) {
  const layers = state.level === "japan" ? ["prefs-fill"] : ["munis-fill", "prefs-fill"];
  const f = map.queryRenderedFeatures(point, { layers })[0];
  if (!f) return null;
  const p = f.properties;
  if (f.layer.id === "prefs-fill") {
    if (state.level !== "japan" && p.code === state.pref?.code) return null;  // inside: municipalities take over
    return { source: "prefs", id: p.code, label: LANG === "ja" ? p.name_ja : `${p.name_en} · ${p.name_ja}` };
  }
  return { source: "munis", id: p.code, label: p.name_ja };
}

map.on("mousemove", (e) => {
  if (!state.prefs) return;
  const target = hoverTarget(e.point);
  if (hovered && (!target || target.id !== hovered.id)) { setFeature(hovered.source, hovered.id, "hover", false); hovered = null; }
  if (target && !hovered) { hovered = target; setFeature(target.source, target.id, "hover", true); }
  const insideSelected = state.level === "muni" && target?.id === state.muni?.code;
  if (target && !insideSelected) {
    tip.textContent = target.label;
    tip.style.left = `${e.point.x + 14}px`;
    tip.style.top = `${e.point.y + 14}px`;
    tip.hidden = false;
  } else {
    tip.hidden = true;
  }
  map.getCanvas().style.cursor = target ? "pointer" : "";
});
map.on("mouseout", () => {
  tip.hidden = true;
  if (hovered) { setFeature(hovered.source, hovered.id, "hover", false); hovered = null; }
});

// ---------- click ----------
map.on("click", (e) => {
  const shelter = map.getLayer("shelters") && map.queryRenderedFeatures(e.point, { layers: ["shelters"] })[0];
  if (shelter) {
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(shelterPopup(shelter.properties)).addTo(map);
    return;
  }
  const muni = state.level !== "japan" && map.queryRenderedFeatures(e.point, { layers: ["munis-fill"] })[0];
  if (muni) {
    if (state.level === "muni" && muni.properties.code === state.muni.code) runReport(e.lngLat.lat, e.lngLat.lng);
    else selectMuni(muni.properties.code);
    return;
  }
  const pref = map.queryRenderedFeatures(e.point, { layers: ["prefs-fill"] })[0];
  if (pref && pref.properties.code !== state.pref?.code) selectPref(pref.properties.code);
});

// ---------- selection ----------
const prefSelect = document.getElementById("pref-select");
const muniSelect = document.getElementById("muni-select");
const crumbs = document.getElementById("crumbs");
const body = document.getElementById("area-body");

function prefName(p) { return LANG === "ja" ? p.name_ja : p.name_en; }
function fit(bbox, opts = {}) {
  map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40, duration: 1400, maxZoom: 15, ...opts });
}

function selectedState(source, prevId, nextId) {
  setFeature(source, prevId, "selected", false);
  setFeature(source, nextId, "selected", true);
}

function goJapan() {
  selectedState("prefs", state.pref?.code, null);
  Object.assign(state, { level: "japan", pref: null, munis: null, muni: null, view: "area" });
  map.getSource("munis").setData(empty());
  map.getSource("mask").setData(empty());
  removeAreaLayers();
  clearPoint();
  map.fitBounds(JAPAN_BOUNDS, { padding: 30, pitch: 0, bearing: 0, duration: 1400 });
  setViewMode(false);
  renderNav();
  renderBody();
}

async function selectPref(code, { fly = true } = {}) {
  await Promise.all([prefsReady, mapReady]);  // a pick from the dropdown can come before the map is ready
  const feat = state.prefs.features.find((f) => f.properties.code === code);
  if (!feat) return;
  selectedState("prefs", state.pref?.code, code);
  Object.assign(state, { level: "pref", pref: feat.properties, muni: null, view: "area" });
  map.getSource("mask").setData(empty());
  removeAreaLayers();
  clearPoint();
  map.setFilter("muni-selected", ["==", ["get", "code"], ""]);
  if (fly) fit(feat.properties.bbox, { maxZoom: 10 });
  renderNav();
  renderBody();
  state.munis = await getJSON(`/areas/prefectures/${code}/municipalities`);
  if (state.pref?.code !== code) return;
  await mapReady;
  map.getSource("munis").setData(state.munis);
  renderNav();
  loadStats("pref", `/areas/prefectures/${code}/stats`, code);
}

async function selectMuni(code, { fly = true, keepPoint = false } = {}) {
  if (state.pref?.code !== code.slice(0, 2)) await selectPref(code.slice(0, 2), { fly: false });
  const m = await getJSON(`/areas/municipalities/${code}`);
  await mapReady;
  selectedState("munis", state.muni?.code, code);
  Object.assign(state, { level: "muni", muni: m, view: keepPoint ? state.view : "area" });
  map.getSource("mask").setData({ type: "Feature", geometry: m.mask, properties: {} });
  map.setFilter("muni-selected", ["==", ["get", "code"], code]);
  addAreaLayers(code);
  if (!keepPoint) clearPoint();
  if (fly) fit(m.bbox);
  const c = [(m.bbox[0] + m.bbox[2]) / 2, (m.bbox[1] + m.bbox[3]) / 2];
  if (!keepPoint) selection = { lat: c[1], lon: c[0], label: `${m.pref_ja}${m.name_ja}`, area_code: code, kind: "area" };
  renderNav();
  if (!keepPoint) { renderBody(); loadStats("muni", `/areas/municipalities/${code}/stats`, code); }
}

// ---------- side panel: picker, breadcrumb ----------
function renderNav() {
  const prefs = state.prefs?.features.map((f) => f.properties) ?? [];
  prefSelect.innerHTML = `<option value="">${t("nav.choosePref")}</option>` +
    prefs.map((p) => `<option value="${p.code}" ${p.code === state.pref?.code ? "selected" : ""}>${esc(prefName(p))}</option>`).join("");
  const munis = state.pref && state.munis ? state.munis.features.map((f) => f.properties) : [];
  muniSelect.disabled = !munis.length;
  muniSelect.innerHTML = `<option value="">${t("nav.chooseMuni")}</option>` +
    munis.map((m) => `<option value="${m.code}" ${m.code === state.muni?.code ? "selected" : ""}>${esc(m.name_ja)}</option>`).join("");
  const parts = [`<li><button type="button" data-go="japan">${t("nav.japan")}</button></li>`];
  if (state.pref) parts.push(`<li><button type="button" data-go="pref">${esc(prefName(state.pref))}</button></li>`);
  if (state.muni) parts.push(`<li><span>${esc(state.muni.name_ja)}</span></li>`);
  crumbs.innerHTML = parts.join("");
  renderLayerPanel();  // area layers are only available once a municipality is selected
  renderLegend();
}

prefSelect.addEventListener("change", () => (prefSelect.value ? selectPref(prefSelect.value) : goJapan()));
muniSelect.addEventListener("change", () => (muniSelect.value ? selectMuni(muniSelect.value) : selectPref(state.pref.code)));
crumbs.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]")?.dataset.go;
  if (go === "japan") goJapan();
  else if (go === "pref") selectPref(state.pref.code);
});

// ---------- side panel: area summaries ----------
function renderBody() {
  if (state.view === "point" && lastReport) return showReport();
  if (state.level === "japan") {
    const munis = state.prefs ? state.prefs.features.reduce((s, f) => s + f.properties.municipalities, 0) : 0;
    body.innerHTML = `<section class="intro"><h2>${t("japan.title")}</h2><p>${t("japan.body")}</p>
      <p class="hint">${t("japan.facts", { prefs: state.prefs?.features.length ?? 47, munis: int(munis) })}</p></section>`;
    return;
  }
  if (lastStats && lastStats.code === (state.level === "pref" ? state.pref.code : state.muni.code)) {
    body.innerHTML = state.level === "pref" ? prefStatsHTML(lastStats.data) : muniStatsHTML(lastStats.data);
  } else {
    const title = state.level === "pref" ? prefName(state.pref) : `${state.muni.pref_ja} ${state.muni.name_ja}`;
    body.innerHTML = `<div class="place"><h2>${esc(title)}</h2></div><div class="loading">${t("stats.loading")}</div>`;
  }
}

async function loadStats(kind, path, code) {
  try {
    const data = await getJSON(path);
    const current = kind === "pref" ? state.level === "pref" && state.pref?.code === code
      : state.level === "muni" && state.muni?.code === code;
    lastStats = { code, data };
    if (current && state.view === "area") renderBody();
  } catch {
    if (state.view === "area") body.insertAdjacentHTML("beforeend", `<div class="error">${t("stats.failed")}</div>`);
  }
}

function statBar(label, frac, { color = COLORS.accent, value, sub = false, nodata = false } = {}) {
  const v = nodata ? t("stats.noData") : (value ?? pct(frac));
  return `<div class="sbar${sub ? " sub" : ""}${nodata ? " nodata" : ""}"><span class="lab">${label}</span><span class="val">${v}</span>
    <span class="track"><span class="fill" style="width:${nodata ? 0 : Math.min(100, frac * 100).toFixed(1)}%;background:${color}"></span></span></div>`;
}

function zonesCard(z, n, has = null) {
  const no = (k) => has && !has[k];
  return `<div class="card"><h3>${t("stats.zonesTitle")}</h3>
    ${statBar(t("stats.zone.flood"), z.flood, { color: DEPTH_CLASSES[2].color, nodata: no("flood") })}
    ${statBar(t("stats.zone.flood_deep"), z.flood_3 + z.flood_4, { color: DEPTH_CLASSES[3].color, sub: true, nodata: no("flood") })}
    ${statBar(t("stats.zone.storm_surge"), z.storm_surge, { color: COLORS.surge, nodata: no("storm_surge") })}
    ${statBar(t("stats.zone.tsunami"), z.tsunami, { color: COLORS.tsunami, nodata: no("tsunami") })}
    ${statBar(t("stats.zone.landslide"), z.landslide, { color: COLORS.slide, nodata: no("landslide") })}
    ${statBar(t("stats.zone.landslide_special"), z.landslide_special, { color: COLORS.slideRed, sub: true, nodata: no("landslide") })}
    <p class="src">${t("stats.zonesNote", { n: int(n) })}</p></div>`;
}

function sheltersCard(s) {
  const tags = HAZARDS.filter((h) => s[h]).map((h) => `<span class="tag on">${t(`hazard.${h}`)} ${int(s[h])}</span>`).join("");
  return `<div class="card"><h3>🏫 ${t("stats.sheltersTitle")}</h3><div class="big">${t("stats.sheltersTotal", { n: int(s.total) })}</div>
    <div class="tags">${tags}</div></div>`;
}

function prefStatsHTML(s) {
  const p = state.pref;
  return `<div class="place"><h2>${esc(prefName(p))}</h2><p>${esc(LANG === "ja" ? p.name_en : p.name_ja)}</p></div>
    <div class="facts"><div><b>${int(Math.round(s.area_km2))} km²</b><span>${t("stats.area")}</span></div>
      <div><b>${int(s.municipalities)}</b><span>${t("stats.municipalities")}</span></div></div>
    <p class="hint-box">${t("pref.hint")}</p>
    ${zonesCard(s.zones, 5000)}
    <div class="card"><h3>🏢 ${t("stats.bldgTitle")}</h3>
      <p>${s.buildings.total ? t("stats.prefBuildings", { n: int(s.buildings.total), c: int(s.buildings.cities) }) : t("stats.noBuildings")}</p></div>
    ${sheltersCard(s.shelters)}`;
}

function muniStatsHTML(s) {
  const m = state.muni, b = s.buildings;
  const missing = Object.entries(s.data_available).filter(([, ok]) => !ok).map(([k]) => t(`dataset.${k}`));
  const notice = missing.length ? `<div class="card notice"><p>${t("notice.partial", { list: esc(missing.join(t("list.sep"))) })}</p></div>` : "";
  let bldg = `<p>${t("stats.noBuildings")}</p>`;
  if (b.total) {
    const classes = [
      [t("stats.depthNone"), b.total - b.flooded, COLORS.noFlood],
      [`&lt; 0.5 ${t("unit.m")}`, b.d0, DEPTH_CLASSES[0].color],
      [range({ min: 0.5, max: 3 }), b.d1, DEPTH_CLASSES[1].color],
      [range({ min: 3, max: 5 }), b.d2, DEPTH_CLASSES[2].color],
      [range({ min: 5, max: null }), b.d3, DEPTH_CLASSES[3].color],
    ];
    bldg = `<div class="big">${t("stats.bldgTotal", { n: int(b.total) })}</div>
      <p>${t("stats.bldgFlooded", { n: int(b.flooded), p: (100 * b.flooded / b.total).toFixed(0) })}</p>
      <p class="sub-h">${t("stats.bldgByDepth")}</p>
      ${classes.map(([lab, n, color]) => statBar(lab, n / b.total, { color, value: int(n) })).join("")}
      ${b.low_rise_deep ? `<div class="verdict lv-high" style="margin-top:10px">${t("stats.lowRiseDeep", { n: int(b.low_rise_deep) })}</div>` : ""}`;
  }
  return `<div class="place"><h2>${esc(`${m.pref_ja} ${m.name_ja}`)}</h2><p>${esc(LANG === "ja" ? m.pref_ja : m.pref_en)} · ${m.code}</p></div>
    <div class="facts"><div><b>${num(s.area_km2)} km²</b><span>${t("stats.area")}</span></div>
      <div><b>${int(b.total)}</b><span>${t("stats.bldgTitle")}</span></div>
      <div><b>${int(s.shelters.total)}</b><span>${t("stats.sheltersTitle")}</span></div></div>
    <p class="hint-box">${t("muni.pointHint")}</p>
    ${notice}
    ${zonesCard(s.zones, s.sample_points, s.data_available)}
    <div class="card"><h3>🏢 ${t("stats.bldgTitle")}</h3>${bldg}<p class="src">${t("bldg.source")}</p></div>
    ${sheltersCard(s.shelters)}
    ${quakeCard(s.earthquake).replace(t("quake.title30"), t("stats.quakeTitle"))}`;
}

// ---------- point report ----------
class ReportError extends Error {}

function clearPoint() {
  if (pointMarker) { pointMarker.remove(); pointMarker = null; }
  lastReport = null;
}

async function runReport(lat, lon, label) {
  if (pointMarker) pointMarker.setLngLat([lon, lat]);
  else pointMarker = new maplibregl.Marker({ color: COLORS.accent }).setLngLat([lon, lat]).addTo(map);
  selection = { lat, lon, label: label || null, kind: "point" };
  state.view = "point";
  lastReport = null;
  body.innerHTML = `<div class="loading">${t("report.checking", { lat: lat.toFixed(5), lon: lon.toFixed(5) })}</div>`;
  try {
    const [rep, addr] = await Promise.all([
      fetch(`/report?lat=${lat}&lon=${lon}`).then(async (r) => {
        if (r.status === 422) throw new ReportError("report.outside");
        if (!r.ok) throw new Error(t("report.serverError", { status: r.status }));
        return r.json();
      }),
      label ? Promise.resolve(label) : reverseGeocode(lat, lon),
    ]);
    const a = rep.location.area;
    selection.label = addr && addr.startsWith(a.pref_ja) ? addr : `${a.pref_ja}${a.name_ja ?? ""}${addr ? ` ${addr}` : ""}`;
    selection.area_code = a.code;
    lastReport = { rep, addr };
    if (state.muni?.code !== a.code) await selectMuni(a.code, { fly: false, keepPoint: true });
    showReport();
  } catch (err) {
    body.innerHTML = `${backLink()}<div class="error">${esc(err instanceof ReportError ? t(err.message) : err.message)}</div>`;
  }
}

function backLink() {
  return state.level === "muni" ? `<p><button type="button" class="link-btn" data-back>${t("muni.backToArea")}</button></p>` : "";
}

function showReport() {
  const { rep, addr } = lastReport;
  body.innerHTML = backLink() + renderReport(rep, addr);
  document.getElementById("panel").scrollTop = 0;
  body.querySelectorAll("[data-shelter]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const [la, lo] = btn.dataset.shelter.split(",").map(Number);
      map.flyTo({ center: [lo, la], zoom: 17 });
      new maplibregl.Popup({ closeButton: false }).setLngLat([lo, la]).setText(btn.textContent).addTo(map);
    }));
}

body.addEventListener("click", (e) => {
  if (e.target.closest("[data-back]")) {
    clearPoint();
    state.view = "area";
    const m = state.muni;
    selection = { lat: (m.bbox[1] + m.bbox[3]) / 2, lon: (m.bbox[0] + m.bbox[2]) / 2, label: `${m.pref_ja}${m.name_ja}`,
      area_code: m.code, kind: "area" };
    renderBody();
  }
});

async function reverseGeocode(lat, lon) {
  try {
    const r = await fetch(`https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat=${lat}&lon=${lon}`);
    const d = await r.json();
    return d.results?.lv01Nm || null;
  } catch { return null; }
}

// ---------- search ----------
document.getElementById("search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = document.getElementById("q").value.trim();
  if (!q) return;
  showTab("report");
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m) return go(+m[1], +m[2]);
  body.innerHTML = `<div class="loading">${esc(t("search.searching", { q }))}</div>`;
  try {
    const r = await fetch(`https://msearch.gsi.go.jp/address-search/AddressSearch?q=${encodeURIComponent(q)}`);
    const hits = await r.json();
    if (!hits.length) throw new Error(t("search.notFound"));
    const [lon, lat] = hits[0].geometry.coordinates;
    go(lat, lon, hits[0].properties.title);
  } catch (err) {
    body.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
});

function go(lat, lon, label) {
  map.flyTo({ center: [lon, lat], zoom: 16, duration: 1600 });
  runReport(lat, lon, label);
}

// ---------- 2D / 3D ----------
const toggleBtn = document.getElementById("view-toggle");
let in3d = false;
function setViewMode(three) {
  in3d = three;
  toggleBtn.textContent = three ? t("map3d.to2d") : t("map3d.to3d");
  toggleBtn.setAttribute("aria-pressed", String(three));
}
toggleBtn.addEventListener("click", () => {
  setViewMode(!in3d);
  map.easeTo({ pitch: in3d ? 60 : 0, bearing: in3d ? -20 : 0, zoom: in3d ? Math.max(map.getZoom(), 15.5) : map.getZoom(), duration: 1000 });
});

// ---------- layer panel and legend ----------
const layerPanel = document.getElementById("layer-panel");
const legendEl = document.getElementById("legend");
let photoOn = false;
let layersOpen = window.innerWidth >= 820;  // collapsed on small screens, where it would cover the map

function renderLayerPanel() {
  // the switcher appears once a municipality is selected: that is when there are layers to switch
  const row = (key, label, checked) =>
    `<label><input type="checkbox" data-layer="${key}" ${checked ? "checked" : ""}> ${label}</label>`;
  layerPanel.innerHTML = `<button type="button" class="layer-head" aria-expanded="${layersOpen}">${t("layers.title")} ${layersOpen ? "▾" : "▸"}</button>` +
    (layersOpen ? Object.keys(AREA_LAYERS).map((k) => row(k, t(`layers.${k}`), AREA_LAYERS[k].visible)).join("") +
      row("photo", t("layers.photo"), photoOn) : "");
  layerPanel.hidden = state.level !== "muni";
}

layerPanel.addEventListener("click", (e) => {
  if (e.target.closest(".layer-head")) { layersOpen = !layersOpen; renderLayerPanel(); }
});
layerPanel.addEventListener("change", (e) => {
  const key = e.target.dataset.layer;
  if (key === "photo") {
    photoOn = e.target.checked;
    map.setLayoutProperty("photo", "visibility", photoOn ? "visible" : "none");
    return;
  }
  AREA_LAYERS[key].visible = e.target.checked;
  for (const id of AREA_LAYERS[key].ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", e.target.checked ? "visible" : "none");
  renderLegend();
});

function renderLegend() {
  if (state.level !== "muni") { legendEl.hidden = true; return; }
  const sw = (color, label) => `<div><i style="background:${color}"></i>${label}</div>`;
  let html = "";
  if (AREA_LAYERS.flood.visible || AREA_LAYERS.buildings.visible) {
    html += `<b>${t("map.legend.title")}</b>` +
      DEPTH_CLASSES.slice(0, 5).map((c) => sw(c.color, c.lo === 0 ? `&lt; ${num(c.hi)} ${t("unit.m")}` : range({ min: c.lo, max: c.hi }))).join("");
  }
  if (AREA_LAYERS.buildings.visible) html += sw(COLORS.water, t("map3d.water")) + sw(COLORS.noFlood, t("map3d.noFlood"));
  if (AREA_LAYERS.landslide.visible) html += `<div><i style="background:${COLORS.slide}"></i><i style="background:${COLORS.slideRed}"></i>${t("legend.landslide")}</div>`;
  if (AREA_LAYERS.storm_surge.visible) html += sw(COLORS.surge, t("legend.surge"));
  if (AREA_LAYERS.tsunami.visible) html += sw(COLORS.tsunami, t("legend.tsunami"));
  if (AREA_LAYERS.buildings.visible) html += `<div class="note">${t("map.legend.buildings", { z: BUILDINGS_MIN_ZOOM })}</div>`;
  legendEl.innerHTML = html;
  legendEl.hidden = !html;
}

// ---------- language switch ----------
document.addEventListener("langchange", () => {
  renderNav();
  renderBody();
  setViewMode(in3d);
});

// ---------- start ----------
// the prefecture list loads in parallel with the map, so the dropdown works before the first tiles arrive
const prefsReady = getJSON("/areas/prefectures").then((fc) => {
  state.prefs = fc;
  renderNav();
  renderBody();
  return fc;
});
const mapReady = new Promise((resolve) => map.on("load", resolve));
Promise.all([prefsReady, mapReady]).then(([fc]) => {
  map.getSource("prefs").setData(fc);
  // open on the whole globe, then fly in to Japan (unless a place was already picked)
  if (state.level === "japan") map.fitBounds(JAPAN_BOUNDS, { padding: 30, duration: 2500 });
});
renderNav();
renderBody();
setViewMode(false);
