"""Point-lookup risk report: every number carries the dataset it came from."""
from __future__ import annotations

import math

from psycopg import Connection
from psycopg.rows import dict_row

from . import jshis

FLOOD_DEPTH_RANK = {1: (0.0, 0.5), 2: (0.5, 3.0), 3: (3.0, 5.0), 4: (5.0, 10.0), 5: (10.0, 20.0), 6: (20.0, None)}
FLOOD_DURATION_RANK = {1: (0, 12), 2: (12, 24), 3: (24, 72), 4: (72, 168), 5: (168, 336), 6: (336, 672), 7: (672, None)}
LANDSLIDE_PHENOMENON = {1: "steep_slope_failure", 2: "debris_flow", 3: "landslide"}
LANDSLIDE_ZONE = {1: "warning", 2: "special_warning", 3: "warning_pending", 4: "special_warning_pending"}
SHELTER_TYPES = ["flood", "landslide", "storm_surge", "earthquake", "tsunami", "fire", "inland_flood", "volcano"]

DEFAULT_STOREY_M = 3.0
SAFETY_MARGIN_M = 0.5
BUILDING_SNAP_M = 30

PT = "ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)"


class OutsideCoverage(Exception):
    pass


def _range(rank, table):
    lo, hi = table.get(rank, (None, None))
    return {"rank": rank, "min": lo, "max": hi}


def _area(cur, p):
    cur.execute(f"""
        SELECT m.code, m.pref_code, m.pref_ja, m.pref_en, m.city_ja, m.ward_ja, m.name_ja
        FROM koko.municipality_parts mp JOIN koko.municipalities m USING (code)
        WHERE ST_Intersects(mp.geom, {PT}) LIMIT 1""", p)
    return cur.fetchone()


def first_mesh(lat: float, lon: float) -> str:
    """JIS 1st-order mesh code; flood data is published per mesh."""
    return f"{int(lat * 1.5):02d}{int(lon) - 100:02d}"


def _coverage(cur, pref: str, mesh: str) -> dict:
    """Which datasets are loaded for this place. 'Not in a zone' only means something where data exists."""
    cur.execute("SELECT layer, src FROM koko.loaded_files WHERE src = %s OR src LIKE %s",
                (pref, f"{mesh}\\_%"))
    have = {(r["layer"], r["src"].split("_")[0]) for r in cur.fetchall()}
    return {
        "flood": ("flood_depth", mesh) in have,
        "landslide": ("landslide", pref) in have,
        "tsunami": ("tsunami", pref) in have,
        "storm_surge": ("storm_surge", pref) in have,
        "shelters": ("shelters", pref) in have,
    }


def _flood(cur, p):
    cur.execute(f"""
        SELECT scale, max(depth_rank) AS rank FROM koko.flood_depth
        WHERE ST_Intersects(geom, {PT}) GROUP BY scale""", p)
    by_scale = {r["scale"]: r["rank"] for r in cur.fetchall()}
    cur.execute(f"SELECT max(duration_rank) AS rank FROM koko.flood_duration WHERE ST_Intersects(geom, {PT})", p)
    dur = cur.fetchone()["rank"]
    cur.execute(f"SELECT array_agg(DISTINCT kind) AS kinds FROM koko.flood_collapse WHERE ST_Intersects(geom, {PT})", p)
    kinds = cur.fetchone()["kinds"] or []
    return {
        "in_zone": bool(by_scale),
        "max_assumed": _range(by_scale["L2"], FLOOD_DEPTH_RANK) | {"unit": "m"} if "L2" in by_scale else None,
        "planned_scale": _range(by_scale["L1"], FLOOD_DEPTH_RANK) | {"unit": "m"} if "L1" in by_scale else None,
        "duration": _range(dur, FLOOD_DURATION_RANK) | {"unit": "h"} if dur else None,
        "house_collapse_zone": {
            "flood_flow": 1 in kinds or 3 in kinds,
            "bank_erosion": 2 in kinds or 3 in kinds,
        },
        "source": "flood",
    }


def _building(cur, p):
    cur.execute(f"""
        SELECT id, usage_label, height_m, storeys_above, storeys_below, address, flood_max_depth_m, risks,
               ST_Distance(geom::geography, {PT}::geography) AS distance_m
        FROM koko.buildings
        WHERE ST_DWithin(geom, {PT}, 0.0005)  -- ~50 m in degrees: index-friendly prefilter
          AND ST_DWithin(geom::geography, {PT}::geography, {BUILDING_SNAP_M})
        ORDER BY geom <-> {PT} LIMIT 1""", p)
    b = cur.fetchone()
    if b:
        b["distance_m"] = round(b["distance_m"], 1)
        b["source"] = "plateau"
    return b


def vertical_evacuation(building: dict | None, flood: dict) -> dict | None:
    """Which floor sits above the expected maximum flood depth.

    Floor n's floor level is approximated as (n - 1) x storey height, and a floor counts as
    safe when that level clears the depth plus a margin. House-collapse zones override this:
    official guidance there is to leave the building, not go upstairs.
    """
    if not flood["in_zone"]:
        return None
    zone = flood["max_assumed"]
    zone_upper = (zone["max"] or zone["min"]) if zone else None
    plateau_depth = building.get("flood_max_depth_m") if building else None

    # PLATEAU assigns each building the mesh value it overlaps most, so it can be lower than the
    # zone at the exact point. Trust it only when it agrees with the zone rank; otherwise take the
    # zone's upper bound. For a safety answer the cautious number wins.
    if plateau_depth is not None and (zone is None or plateau_depth >= zone["min"]):
        depth, basis = plateau_depth, "plateau_building"
    elif zone_upper is not None:
        depth, basis = zone_upper, "zone_rank_upper_bound"
    else:
        return None

    collapse = flood["house_collapse_zone"]["flood_flow"] or flood["house_collapse_zone"]["bank_erosion"]
    long_duration = bool(flood["duration"] and flood["duration"]["rank"] >= 3)  # 24 h or more
    result = {"expected_depth_m": depth, "depth_basis": basis, "plateau_building_depth_m": plateau_depth,
              "house_collapse_zone": collapse, "isolation_24h_or_more": long_duration}

    if collapse:
        return result | {"verdict": "leave_building",
                         "reason": "Inside a house-collapse flood zone (家屋倒壊等氾濫想定区域): evacuate early, do not shelter upstairs."}
    if not building or not building.get("storeys_above"):
        return result | {"verdict": "unknown", "reason": "No building storey data at this point."}

    storeys = building["storeys_above"]
    storey_m = DEFAULT_STOREY_M
    if building.get("height_m") and 2.5 <= building["height_m"] / storeys <= 6:
        storey_m = building["height_m"] / storeys
    safe_floor = math.ceil((depth + SAFETY_MARGIN_M) / storey_m) + 1
    result |= {"storey_height_m": round(storey_m, 2), "storeys_above": storeys,
               "lowest_safe_floor": safe_floor, "safety_margin_m": SAFETY_MARGIN_M}
    if safe_floor > storeys:
        return result | {"verdict": "leave_building",
                         "reason": "No floor of this building is clearly above the expected flood depth."}
    reason = f"Floors {safe_floor} and above are above the expected maximum depth."
    if long_duration:
        reason += " Water may stay for 24 h or more: sheltering upstairs means being cut off, so leaving early is safer."
    return result | {"verdict": "upper_floors_ok" if safe_floor > 1 else "minor", "reason": reason}


def _landslide(cur, p):
    cur.execute(f"""
        SELECT DISTINCT phenomenon, zone, name FROM koko.landslide
        WHERE ST_Intersects(geom, {PT}) ORDER BY zone DESC""", p)
    return [{"phenomenon": LANDSLIDE_PHENOMENON.get(r["phenomenon"]), "zone": LANDSLIDE_ZONE.get(r["zone"]),
             "name": r["name"], "source": "landslide"} for r in cur.fetchall()]


def _depth_zone(cur, p, table, source):
    cur.execute(f"""
        SELECT depth_label, depth_min_m, depth_max_m FROM koko.{table}
        WHERE ST_Intersects(geom, {PT})
        ORDER BY depth_min_m DESC NULLS LAST LIMIT 1""", p)
    r = cur.fetchone()
    return ({"in_zone": True, "depth_label": r["depth_label"], "min": r["depth_min_m"], "max": r["depth_max_m"],
             "unit": "m", "source": source} if r else {"in_zone": False, "source": source})


def _shelter_rows(cur, p, where: str = "TRUE", limit: int = 5):
    cur.execute(f"""
        SELECT name, address, remarks, {", ".join(SHELTER_TYPES)},
               ST_X(geom) AS lon, ST_Y(geom) AS lat,
               round(ST_Distance(geom::geography, {PT}::geography)) AS distance_m
        FROM koko.shelters WHERE {where} ORDER BY geom <-> {PT} LIMIT %(limit)s""", p | {"limit": limit})
    return [{"name": r["name"], "address": r["address"], "remarks": r["remarks"],
             "lat": r["lat"], "lon": r["lon"], "distance_m": int(r["distance_m"]), "straight_line": True,
             "for": [t for t in SHELTER_TYPES if r[t]], "source": "shelters"} for r in cur.fetchall()]


def _shelters(cur, p, hazards: list[str]):
    """Nearest sites overall, plus the nearest site rated for each hazard present at this point."""
    by_hazard = {}
    for h in hazards:
        rows = _shelter_rows(cur, p, where=h, limit=1)  # h is from SHELTER_TYPES, a fixed column name
        by_hazard[h] = rows[0] if rows else None
    return {"nearest": _shelter_rows(cur, p), "nearest_for_hazard": by_hazard}


def _sources(cur):
    cur.execute("SELECT source, title, url FROM koko.source_meta ORDER BY source")
    out = {r["source"]: r for r in cur.fetchall()}
    out["jshis"] = {"source": "jshis", "title": jshis.TITLE, "url": jshis.URL}
    return out


def build_report(conn: Connection, lat: float, lon: float, *, with_earthquake: bool = True) -> dict:
    p = {"lat": lat, "lon": lon}
    with conn.cursor(row_factory=dict_row) as cur:
        area = _area(cur, p)
        if not area:
            raise OutsideCoverage("No data for this location: it is outside Japan, at sea, "
                                  "or its prefecture has not been loaded yet.")
        coverage = _coverage(cur, area["pref_code"], first_mesh(lat, lon))
        flood = _flood(cur, p)
        building = _building(cur, p)
        landslide = _landslide(cur, p)
        storm_surge = _depth_zone(cur, p, "storm_surge", "storm_surge")
        tsunami = _depth_zone(cur, p, "tsunami", "tsunami")
        hazards = ["earthquake"] + [h for h, present in (
            ("flood", flood["in_zone"]), ("landslide", bool(landslide)),
            ("storm_surge", storm_surge["in_zone"]), ("tsunami", tsunami["in_zone"])) if present]
        report = {
            "location": {"lat": lat, "lon": lon, "area": area},
            "data_available": coverage,
            "flood": flood,
            "building": building,
            "vertical_evacuation": vertical_evacuation(building, flood),
            "landslide": landslide,
            "storm_surge": storm_surge,
            "tsunami": tsunami,
            "shelters": _shelters(cur, p, hazards),
            "sources": _sources(cur),
        }
    report["earthquake"] = jshis.shaking_probability(lat, lon) if with_earthquake else None
    report["disclaimer"] = ("Based on official hazard assumptions, not predictions. Not being in a zone does not "
                            "mean zero risk. In an emergency follow JMA warnings and municipal evacuation orders.")
    return report
