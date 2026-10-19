"""KOKO API."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .report import OutsideCoverage, build_report

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://koko:koko@127.0.0.1:5432/koko")
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    yield
    pool.close()


app = FastAPI(title="KOKO API", version="0.1.0", lifespan=lifespan,
              description="Address-level disaster risk for Japan, built on official open data.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.get("/health")
def health():
    with pool.connection() as conn:
        n = conn.execute("SELECT count(*) FROM koko.wards").fetchone()[0]
    return {"status": "ok", "wards": n}


@app.get("/wards")
def wards():
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT code, name_ja, name_en,
                              EXISTS (SELECT 1 FROM koko.buildings b WHERE b.ward_code = w.code) AS has_buildings
                       FROM koko.wards w ORDER BY code""")
        return cur.fetchall()


def _feature_collection(cur, sql: str, params: dict | None = None) -> dict:
    """Run a query whose rows are GeoJSON Feature objects and wrap them."""
    cur.execute(f"SELECT coalesce(json_agg(f), '[]'::json) FROM ({sql}) AS q(f)", params or {})
    return {"type": "FeatureCollection", "features": cur.fetchone()[0]}


@app.get("/layers/wards")
def layer_wards():
    with pool.connection() as conn, conn.cursor() as cur:
        return _feature_collection(cur, """
            SELECT json_build_object('type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0002), 5)::json,
                'properties', json_build_object('code', code, 'name_ja', name_ja, 'name_en', name_en))
            FROM koko.wards""")


@app.get("/layers/shelters")
def layer_shelters():
    with pool.connection() as conn, conn.cursor() as cur:
        return _feature_collection(cur, """
            SELECT json_build_object('type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 6)::json,
                'properties', json_build_object('name', name, 'address', address, 'remarks', remarks,
                    'flood', flood, 'landslide', landslide, 'storm_surge', storm_surge, 'earthquake', earthquake,
                    'tsunami', tsunami, 'fire', fire, 'inland_flood', inland_flood))
            FROM koko.shelters""")


@app.get("/layers/buildings")
def layer_buildings(bbox: str = Query(..., description="west,south,east,north"), limit: int = Query(4000, le=8000)):
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox must be west,south,east,north")
    if (e - w) * (n - s) > 0.0004:  # roughly 2 km x 2 km; zoom in for building footprints
        raise HTTPException(status_code=422, detail="bbox too large; zoom in")
    with pool.connection() as conn, conn.cursor() as cur:
        return _feature_collection(cur, """
            SELECT json_build_object('type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 6)::json,
                'properties', json_build_object('id', id, 'usage', usage_label, 'storeys', storeys_above,
                    'height_m', height_m, 'flood_depth_m', flood_max_depth_m))
            FROM koko.buildings
            WHERE geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
            LIMIT %(limit)s""", {"w": w, "s": s, "e": e, "n": n, "limit": limit})


@app.get("/report")
def report(lat: float = Query(..., ge=20, le=46), lon: float = Query(..., ge=122, le=154),
           earthquake: bool = Query(True, description="include the live J-SHIS lookup")):
    with pool.connection() as conn:
        try:
            return build_report(conn, lat, lon, with_earthquake=earthquake)
        except OutsideCoverage as e:
            raise HTTPException(status_code=422, detail=str(e))


# The dashboard (plain HTML + Leaflet). Mounted last so the API routes above take priority.
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
