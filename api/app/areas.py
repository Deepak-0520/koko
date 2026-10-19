"""Drill-down areas: prefecture and municipality boundaries, per-area statistics, and vector tiles
of every layer filtered to one municipality (so the map loads only the selected place)."""
from __future__ import annotations

import json
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Response
from psycopg.rows import dict_row

from . import jshis
from .report import FLOOD_DEPTH_RANK, SHELTER_TYPES

router = APIRouter()
_pool = None  # set by main.py


def init(pool) -> None:
    global _pool
    _pool = pool


def _fc(rows: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(r.pop("geometry")), "properties": r} for r in rows]}


def _bbox(r: dict) -> list[float]:
    return [round(r.pop("xmin"), 5), round(r.pop("ymin"), 5), round(r.pop("xmax"), 5), round(r.pop("ymax"), 5)]


# ---------- boundaries ----------

@lru_cache(maxsize=1)
def _prefectures() -> dict:
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            -- country view: drop islets under ~0.1 km2 and simplify to ~1 km; detail returns per municipality
            SELECT code, name_ja, name_en, municipalities,
                   ST_XMin(geom) xmin, ST_YMin(geom) ymin, ST_XMax(geom) xmax, ST_YMax(geom) ymax,
                   ST_AsGeoJSON(coalesce(
                       (SELECT ST_Multi(ST_Collect(ST_Simplify(d.geom, 0.01)))
                        FROM ST_Dump(geom) d WHERE ST_Area(d.geom) > 0.00001 AND ST_Simplify(d.geom, 0.01) IS NOT NULL),
                       ST_Simplify(geom, 0.01)), 3) AS geometry
            FROM koko.prefectures ORDER BY code""")
        rows = cur.fetchall()
    for r in rows:
        r["bbox"] = _bbox(r)
    return _fc(rows)


@router.get("/areas/prefectures")
def prefectures():
    return _prefectures()


@lru_cache(maxsize=64)
def _municipalities(pref: str) -> dict:
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT code, name_ja, city_ja, ward_ja,
                   ST_XMin(geom_display) xmin, ST_YMin(geom_display) ymin,
                   ST_XMax(geom_display) xmax, ST_YMax(geom_display) ymax,
                   ST_AsGeoJSON(geom_display, 5) AS geometry
            FROM koko.municipalities WHERE pref_code = %s ORDER BY code""", (pref,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, "unknown prefecture")
    for r in rows:
        r["bbox"] = _bbox(r)
    return _fc(rows)


@router.get("/areas/prefectures/{pref}/municipalities")
def municipalities(pref: str):
    return _municipalities(pref)


@router.get("/areas/municipalities/{code}")
def municipality(code: str):
    """One municipality: names, bbox, and a mask (Japan minus this area) to dim everything else."""
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT code, pref_code, pref_ja, pref_en, name_ja, city_ja, ward_ja,
                   ST_XMin(geom_display) xmin, ST_YMin(geom_display) ymin,
                   ST_XMax(geom_display) xmax, ST_YMax(geom_display) ymax,
                   ST_AsGeoJSON(ST_Difference(ST_MakeEnvelope(118, 18, 160, 50, 4326), geom_display), 5) AS mask
            FROM koko.municipalities WHERE code = %s""", (code,))
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "unknown municipality")
    r["bbox"] = _bbox(r)
    r["mask"] = json.loads(r["mask"])
    return r


# ---------- statistics ----------

DEPTH_CLASSES = [(0, 0.5), (0.5, 3), (3, 5), (5, None)]  # PLATEAU per-building depth buckets (m)


def _data_version(cur) -> str:
    cur.execute("SELECT coalesce(max(loaded_at)::text, '') AS v FROM koko.loaded_files")
    return cur.fetchone()["v"]


def _cached(cur, code: str, version: str):
    cur.execute("SELECT stats FROM koko.area_stats WHERE code = %s AND data_version = %s", (code, version))
    r = cur.fetchone()
    return r["stats"] if r else None


def _store(cur, code: str, version: str, stats: dict) -> None:
    cur.execute("""INSERT INTO koko.area_stats (code, stats, data_version) VALUES (%s, %s, %s)
                   ON CONFLICT (code) DO UPDATE SET stats = EXCLUDED.stats, data_version = EXCLUDED.data_version,
                   computed_at = now()""", (code, json.dumps(stats, ensure_ascii=False), version))


def _hazard_shares(cur, area_sql: str, params: dict, n: int) -> dict:
    """Share of an area inside each hazard zone, estimated from n seeded random points
    (cheap and deterministic, instead of exact polygon overlays over millions of polygons)."""
    cur.execute(f"""
        WITH pts AS (SELECT (ST_Dump(ST_GeneratePoints(({area_sql}), %(n)s, 42))).geom AS p)
        SELECT
          count(*) AS n,
          count(*) FILTER (WHERE fl.r IS NOT NULL) AS flood,
          count(*) FILTER (WHERE fl.r = 1) AS flood_1, count(*) FILTER (WHERE fl.r = 2) AS flood_2,
          count(*) FILTER (WHERE fl.r = 3) AS flood_3, count(*) FILTER (WHERE fl.r >= 4) AS flood_4,
          count(*) FILTER (WHERE ls.z IS NOT NULL) AS landslide,
          count(*) FILTER (WHERE ls.z IN (2, 4)) AS landslide_special,
          count(*) FILTER (WHERE ss.d IS NOT NULL) AS storm_surge,
          count(*) FILTER (WHERE ts.d IS NOT NULL) AS tsunami
        FROM pts
        LEFT JOIN LATERAL (SELECT max(depth_rank) r FROM koko.flood_depth f
                           WHERE f.scale = 'L2' AND ST_Intersects(f.geom, pts.p)) fl ON true
        LEFT JOIN LATERAL (SELECT max(zone) z FROM koko.landslide l WHERE ST_Intersects(l.geom, pts.p)) ls ON true
        LEFT JOIN LATERAL (SELECT max(depth_min_m) d FROM koko.storm_surge s WHERE ST_Intersects(s.geom, pts.p)) ss ON true
        LEFT JOIN LATERAL (SELECT max(depth_min_m) d FROM koko.tsunami t WHERE ST_Intersects(t.geom, pts.p)) ts ON true
        """, params | {"n": n})
    r = cur.fetchone()
    total = r.pop("n") or 1
    return {k: round(v / total, 4) for k, v in r.items()}


def _coverage(cur, pref: str, bbox: list[float]) -> dict:
    meshes = {f"{int(lat * 1.5):02d}{int(lon) - 100:02d}" for lat in (bbox[1], bbox[3]) for lon in (bbox[0], bbox[2])}
    cur.execute("""SELECT layer, src FROM koko.loaded_files
                   WHERE src = %s OR split_part(src, '_', 1) = ANY(%s)""", (pref, list(meshes)))
    have = {(r["layer"], r["src"].split("_")[0]) for r in cur.fetchall()}
    return {
        "flood": any(("flood_depth", m) in have for m in meshes),
        "landslide": ("landslide", pref) in have,
        "tsunami": ("tsunami", pref) in have,
        "storm_surge": ("storm_surge", pref) in have,
        "shelters": ("shelters", pref) in have,
    }


@router.get("/areas/municipalities/{code}/stats")
def municipality_stats(code: str):
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        version = _data_version(cur)
        if (hit := _cached(cur, code, version)) is not None:
            return hit
        cur.execute("""SELECT pref_code, ST_Area(geom::geography) / 1e6 AS area_km2,
                              ST_X(ST_PointOnSurface(geom_display)) AS lon, ST_Y(ST_PointOnSurface(geom_display)) AS lat,
                              ST_XMin(geom_display) xmin, ST_YMin(geom_display) ymin,
                              ST_XMax(geom_display) xmax, ST_YMax(geom_display) ymax
                       FROM koko.municipalities WHERE code = %s""", (code,))
        m = cur.fetchone()
        if not m:
            raise HTTPException(404, "unknown municipality")
        bbox = _bbox(m)
        p = {"code": code}
        area_sql = "SELECT geom_display FROM koko.municipalities WHERE code = %(code)s"
        n = int(min(4000, max(400, m["area_km2"] * 25)))  # ~200 m spacing for small municipalities

        # buildings: a building belongs to the municipality its centroid falls in
        cur.execute("""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE b.flood_max_depth_m > 0) AS flooded,
                   count(*) FILTER (WHERE b.flood_max_depth_m > 0 AND b.flood_max_depth_m < 0.5) AS d0,
                   count(*) FILTER (WHERE b.flood_max_depth_m >= 0.5 AND b.flood_max_depth_m < 3) AS d1,
                   count(*) FILTER (WHERE b.flood_max_depth_m >= 3 AND b.flood_max_depth_m < 5) AS d2,
                   count(*) FILTER (WHERE b.flood_max_depth_m >= 5) AS d3,
                   count(*) FILTER (WHERE b.storeys_above <= 2 AND b.flood_max_depth_m >= 3) AS low_rise_deep,
                   round(avg(b.storeys_above)::numeric, 1) AS avg_storeys
            FROM koko.buildings b
            JOIN koko.municipality_parts mp
              ON mp.code = %(code)s
             AND b.geom && mp.geom  -- index-friendly prefilter before the centroid test
             AND ST_Intersects(mp.geom, ST_Centroid(b.geom))""", p)
        b = cur.fetchone()

        cur.execute(f"""
            SELECT count(*) AS total, {", ".join(f"count(*) FILTER (WHERE s.{t}) AS {t}" for t in SHELTER_TYPES)}
            FROM koko.shelters s
            JOIN koko.municipality_parts mp ON mp.code = %(code)s AND ST_Intersects(mp.geom, s.geom)""", p)
        shelters = cur.fetchone()

        stats = {
            "code": code,
            "area_km2": round(m["area_km2"], 2),
            "sample_points": n,
            "zones": _hazard_shares(cur, area_sql, p, n),
            "buildings": {k: (float(v) if k == "avg_storeys" and v is not None else v) for k, v in b.items()},
            "shelters": shelters,
            "earthquake": jshis.shaking_probability(m["lat"], m["lon"]),
            "data_available": _coverage(cur, m["pref_code"], bbox),
            "flood_depth_classes": {str(k): v for k, v in FLOOD_DEPTH_RANK.items()},
        }
        _store(cur, code, version, stats)
        conn.commit()
        return stats


@router.get("/areas/prefectures/{pref}/stats")
def prefecture_stats(pref: str):
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        version = _data_version(cur)
        if (hit := _cached(cur, pref, version)) is not None:
            return hit
        cur.execute("""SELECT count(*) AS municipalities, sum(ST_Area(geom::geography)) / 1e6 AS area_km2
                       FROM koko.municipalities WHERE pref_code = %s""", (pref,))
        m = cur.fetchone()
        if not m["municipalities"]:
            raise HTTPException(404, "unknown prefecture")
        cur.execute(f"""SELECT count(*) AS total, {", ".join(f"count(*) FILTER (WHERE {t}) AS {t}" for t in SHELTER_TYPES)}
                        FROM koko.shelters WHERE src = %s""", (pref,))
        shelters = cur.fetchone()
        # PLATEAU datasets are keyed by city code, whose first two digits are the prefecture
        cur.execute("""SELECT count(*) AS total, count(*) FILTER (WHERE flood_max_depth_m > 0) AS flooded,
                              count(DISTINCT src) AS cities
                       FROM koko.buildings WHERE left(src, 2) = %s""", (pref,))
        bldg = cur.fetchone()
        stats = {
            "code": pref,
            "municipalities": m["municipalities"],
            "area_km2": round(m["area_km2"], 1),
            "zones": _hazard_shares(cur, "SELECT geom FROM koko.prefectures WHERE code = %(code)s", {"code": pref}, 5000),
            "buildings": bldg,
            "shelters": shelters,
        }
        _store(cur, pref, version, stats)
        conn.commit()
        return stats


@router.get("/areas/municipalities/{code}/shelters")
def municipality_shelters(code: str):
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""
            SELECT s.name, s.address, s.remarks, {", ".join(f"s.{t}" for t in SHELTER_TYPES)},
                   ST_AsGeoJSON(s.geom, 6) AS geometry
            FROM koko.shelters s
            JOIN koko.municipality_parts mp ON mp.code = %s AND ST_Intersects(mp.geom, s.geom)""", (code,))
        return _fc(cur.fetchall())


# ---------- vector tiles, filtered to one municipality ----------

TILE_LAYERS = {  # layer -> (table, select list, extra filter)
    "buildings": ("koko.buildings", """t.id, t.usage_label AS usage, t.storeys_above AS storeys, t.height_m,
                   t.flood_max_depth_m AS flood_depth_m,
                   coalesce(nullif(t.height_m, 0), t.storeys_above * 3.2, 6) AS h""", ""),
    "flood": ("koko.flood_depth", "t.depth_rank", "AND t.scale = 'L2'"),
    "landslide": ("koko.landslide", "t.zone, t.phenomenon", ""),
    "storm_surge": ("koko.storm_surge", "t.depth_min_m, t.depth_max_m", ""),
    "tsunami": ("koko.tsunami", "t.depth_min_m, t.depth_max_m", ""),
}


@router.get("/tiles/{layer}/{z}/{x}/{y}.pbf")
def tile(layer: str, z: int, x: int, y: int, muni: str = Query(..., pattern=r"^\d{5}$")):
    if layer not in TILE_LAYERS:
        raise HTTPException(404, "unknown layer")
    table, cols, extra = TILE_LAYERS[layer]
    with _pool.connection() as conn:
        row = conn.execute(f"""
            WITH env AS (SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS e3857,
                                ST_Transform(ST_TileEnvelope(%(z)s, %(x)s, %(y)s), 4326) AS e4326)
            SELECT ST_AsMVT(q, %(layer)s, 4096, 'geom') FROM (
                SELECT {cols},
                       ST_AsMVTGeom(ST_Transform(t.geom, 3857), env.e3857, 4096, 64, true) AS geom
                FROM {table} t, env
                WHERE t.geom && env.e4326 {extra}
                  AND EXISTS (SELECT 1 FROM koko.municipality_parts mp
                              WHERE mp.code = %(muni)s AND mp.geom && env.e4326 AND ST_Intersects(mp.geom, t.geom))
            ) q WHERE q.geom IS NOT NULL""", {"z": z, "x": x, "y": y, "layer": layer, "muni": muni}).fetchone()
    return Response(content=bytes(row[0] or b""), media_type="application/vnd.mapbox-vector-tile",
                    headers={"Cache-Control": "public, max-age=600"})
