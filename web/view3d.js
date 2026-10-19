/* 3D view: PLATEAU buildings extruded to their real height (MapLibre GL), coloured by the flood depth
   PLATEAU assigns to each building, with a translucent blue block showing how high that water reaches.
   Uses map, t, num, fmtM, esc, usageLabel, DEPTH_CLASSES, runReport from app.js / i18n.js. */
"use strict";

const map3dEl = document.getElementById("map3d");
const toggleBtn = document.getElementById("view-toggle");
const legend3d = document.getElementById("legend3d");
let map3d = null;
let in3d = false;
let bldgReq3d = 0;

const DEFAULT_STOREY_M = 3.2;
const MIN_ZOOM_3D = 14.5;  // MapLibre zoom (512 px tiles) ~ Leaflet zoom 15.5

/* flood depth (m) -> the same MLIT legend colours as the 2D map; no depth (missing or null) -> light grey */
const DEPTH = ["coalesce", ["get", "flood_depth_m"], 0];
const depthColor = ["case",
  ["<=", DEPTH, 0], "#e4e7eb",
  ["step", DEPTH, DEPTH_CLASSES[0].color,
    0.5, DEPTH_CLASSES[1].color, 3, DEPTH_CLASSES[2].color, 5, DEPTH_CLASSES[3].color,
    10, DEPTH_CLASSES[4].color, 20, DEPTH_CLASSES[5].color]];

function buildingHeight(p) {
  if (p.height_m > 0) return p.height_m;
  if (p.storeys > 0) return p.storeys * DEFAULT_STOREY_M;
  return 6;
}

function create3d() {
  const c = map.getCenter();
  map3d = new maplibregl.Map({
    container: map3dEl,
    center: [c.lng, c.lat],
    zoom: Math.max(map.getZoom() - 1, MIN_ZOOM_3D + 0.5),
    pitch: 60,
    bearing: -20,
    maxPitch: 75,
    attributionControl: { compact: true },
    style: {
      version: 8,
      sources: {
        base: {
          type: "raster", tileSize: 256, maxzoom: 18,
          tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
          attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>',
        },
        flood: {
          type: "raster", tileSize: 256, maxzoom: 17,
          tiles: ["https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_data/{z}/{x}/{y}.png"],
          attribution: '<a href="https://disaportal.gsi.go.jp/" target="_blank">ハザードマップポータルサイト</a>',
        },
        buildings: { type: "geojson", data: { type: "FeatureCollection", features: [] }, attribution: "PLATEAU (MLIT)" },
      },
      layers: [
        { id: "base", type: "raster", source: "base" },
        { id: "flood", type: "raster", source: "flood", paint: { "raster-opacity": 0.45 } },
        {
          // the part of each building below the expected flood depth, in blue: the water line on the building
          id: "water", type: "fill-extrusion", source: "buildings",
          filter: [">", DEPTH, 0],
          paint: {
            "fill-extrusion-color": "#2f7fd8",
            "fill-extrusion-height": DEPTH,
            "fill-extrusion-opacity": 1,
          },
        },
        {
          // the part above the water, coloured by depth class; a building shorter than the water is all blue
          id: "bldg", type: "fill-extrusion", source: "buildings",
          paint: {
            "fill-extrusion-color": depthColor,
            "fill-extrusion-base": ["min", DEPTH, ["get", "h"]],
            "fill-extrusion-height": ["get", "h"],
            "fill-extrusion-opacity": 1,
          },
        },
      ],
    },
  });
  map3d.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  map3d.on("moveend", refresh3d);
  map3d.on("load", refresh3d);
  map3d.on("click", "bldg", (e) => {
    const f = e.features[0], p = f.properties;
    const html = `<b>${esc(p.usage ? usageLabel(p.usage) : t("map.building"))}</b><br>` +
      `${t("bldg.floors")}: ${p.storeys ?? "?"} · ${t("bldg.height")}: ${fmtM(p.height_m > 0 ? p.height_m : null)}<br>` +
      `${t("bldg.plateauDepth")}: ${p.flood_depth_m > 0 ? fmtM(p.flood_depth_m) : t("bldg.none")}`;
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(html).addTo(map3d);
    runReport(e.lngLat.lat, e.lngLat.lng);  // full report in the side panel, same as a 2D click
  });
  map3d.on("mouseenter", "bldg", () => { map3d.getCanvas().style.cursor = "pointer"; });
  map3d.on("mouseleave", "bldg", () => { map3d.getCanvas().style.cursor = ""; });
}

/* Buildings around the view centre. A tilted view reaches the horizon, so the request is a box
   around the centre (within the API's size limit) instead of the full visible bounds. */
async function refresh3d() {
  if (!in3d) return;
  const src = map3d.getSource("buildings");
  if (map3d.getZoom() < MIN_ZOOM_3D) {
    src.setData({ type: "FeatureCollection", features: [] });
    show3dHint(true);
    return;
  }
  show3dHint(false);
  const c = map3d.getCenter();
  const dx = 0.025, dy = 0.02;
  const id = ++bldgReq3d;
  try {
    const r = await fetch(`/layers/buildings?bbox=${[c.lng - dx, c.lat - dy, c.lng + dx, c.lat + dy].map((v) => v.toFixed(5)).join(",")}&limit=15000`);
    if (!r.ok || id !== bldgReq3d) return;
    const fc = await r.json();
    if (id !== bldgReq3d) return;
    for (const f of fc.features) f.properties.h = buildingHeight(f.properties);
    src.setData(fc);
    legend3d.querySelector(".count").textContent = t("map3d.count", { n: fc.features.length.toLocaleString(locale()) });
  } catch { /* offline: keep the view */ }
}

function show3dHint(on) {
  legend3d.querySelector(".hint3d").hidden = !on;
}

function renderLegend3d() {
  legend3d.innerHTML = `<b>${t("map3d.title")}</b>
    <div><i style="background:#e4e7eb"></i>${t("map3d.noFlood")}</div>
    ${DEPTH_CLASSES.slice(0, 4).map((c) => `<div><i style="background:${c.color}"></i>${c.lo === 0 ? `&lt; ${num(c.hi)} ${t("unit.m")}` : range({ min: c.lo, max: c.hi })}</div>`).join("")}
    <div><i style="background:#2f7fd8;opacity:.6"></i>${t("map3d.water")}</div>
    <div class="note">${t("map3d.height")}</div>
    <div class="note count"></div>
    <div class="note hint3d" hidden>${t("map3d.zoomIn")}</div>`;
}

function setView(three) {
  in3d = three;
  map3dEl.hidden = !three;
  legend3d.hidden = !three;
  toggleBtn.textContent = three ? t("map3d.to2d") : t("map3d.to3d");
  toggleBtn.setAttribute("aria-pressed", String(three));
  if (three) {
    renderLegend3d();
    if (!map3d) create3d();
    else {
      const c = map.getCenter();
      map3d.jumpTo({ center: [c.lng, c.lat], zoom: Math.max(map.getZoom() - 1, MIN_ZOOM_3D + 0.5) });
      map3d.resize();
      refresh3d();
    }
  } else if (map3d) {
    const c = map3d.getCenter();
    map.setView([c.lat, c.lng], Math.round(map3d.getZoom() + 1));
    map.invalidateSize();
  }
}

toggleBtn.addEventListener("click", () => setView(!in3d));
document.addEventListener("langchange", () => {
  toggleBtn.textContent = in3d ? t("map3d.to2d") : t("map3d.to3d");
  if (in3d) { renderLegend3d(); refresh3d(); }
});
toggleBtn.textContent = t("map3d.to3d");
