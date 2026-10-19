/* Point report rendering (the detailed card stack for one clicked spot). Uses t/esc/num/fmtM/range/hours from app.js. */
"use strict";

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
  // fixed order: cached statistics come back from jsonb with keys sorted, which would put 5+ before 5-
  const order = ["5-", "5+", "6-", "6+"].filter((k) => eq.probability_30y[k] != null);
  const bars = order.map((k) => [k, eq.probability_30y[k]]).map(([k, p]) =>
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

