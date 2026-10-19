"""KOKO API."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from typing import Literal

from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # before reading DATABASE_URL / ANTHROPIC_API_KEY

from . import assistant  # noqa: E402
from .report import OutsideCoverage, build_report  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://koko:koko@127.0.0.1:5432/koko")
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    yield
    pool.close()


app = FastAPI(title="KOKO API", version="0.1.0", lifespan=lifespan,
              description="Address-level disaster risk for Japan, built on official open data.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


@app.get("/health")
def health():
    with pool.connection() as conn:
        n = conn.execute("SELECT count(*) FROM koko.municipalities").fetchone()[0]
    return {"status": "ok", "municipalities": n}


@app.get("/coverage")
def coverage():
    """What is loaded: prefectures with their layers, and PLATEAU building datasets."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT m.pref_code, min(m.pref_en) AS pref_en, min(m.pref_ja) AS pref_ja,
                              count(*) AS municipalities,
                              (SELECT array_agg(DISTINCT layer ORDER BY layer) FROM koko.loaded_files f
                               WHERE f.src = m.pref_code) AS layers
                       FROM koko.municipalities m GROUP BY m.pref_code ORDER BY m.pref_code""")
        prefs = cur.fetchall()
        cur.execute("SELECT src, rows FROM koko.loaded_files WHERE layer = 'buildings' ORDER BY src")
        return {"prefectures": prefs, "building_datasets": cur.fetchall()}


def _feature_collection(cur, sql: str, params: dict | None = None) -> dict:
    """Run a query whose rows are GeoJSON Feature objects and wrap them."""
    cur.execute(f"SELECT coalesce(json_agg(f), '[]'::json) FROM ({sql}) AS q(f)", params or {})
    return {"type": "FeatureCollection", "features": cur.fetchone()[0]}


def _bbox(bbox: str, max_area: float) -> dict:
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox must be west,south,east,north")
    if (e - w) * (n - s) > max_area:
        raise HTTPException(status_code=422, detail="bbox too large; zoom in")
    return {"w": w, "s": s, "e": e, "n": n}


ENVELOPE = "ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)"


@app.get("/layers/areas")
def layer_areas(bbox: str = Query(..., description="west,south,east,north")):
    """Municipality outlines in view, simplified to the view size."""
    p = _bbox(bbox, 25)  # about 5° x 5°
    p["tol"] = max(p["e"] - p["w"], p["n"] - p["s"]) / 1500
    with pool.connection() as conn, conn.cursor() as cur:
        return _feature_collection(cur, f"""
            SELECT json_build_object('type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, %(tol)s), 5)::json,
                'properties', json_build_object('code', code, 'name_ja', name_ja, 'pref_ja', pref_ja, 'pref_en', pref_en))
            FROM koko.municipalities WHERE geom && {ENVELOPE}""", p)


@app.get("/layers/shelters")
def layer_shelters(bbox: str = Query(..., description="west,south,east,north"), limit: int = Query(3000, le=5000)):
    p = _bbox(bbox, 0.25) | {"limit": limit}  # about 50 km x 50 km
    with pool.connection() as conn, conn.cursor() as cur:
        return _feature_collection(cur, f"""
            SELECT json_build_object('type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 6)::json,
                'properties', json_build_object('name', name, 'address', address, 'remarks', remarks,
                    'flood', flood, 'landslide', landslide, 'storm_surge', storm_surge, 'earthquake', earthquake,
                    'tsunami', tsunami, 'fire', fire, 'inland_flood', inland_flood, 'volcano', volcano))
            FROM koko.shelters WHERE geom && {ENVELOPE} LIMIT %(limit)s""", p)


@app.get("/layers/buildings")
def layer_buildings(bbox: str = Query(..., description="west,south,east,north"), limit: int = Query(8000, le=15000)):
    p = _bbox(bbox, 0.003)  # about 5 km x 6 km: a wide screen at zoom 16; zoom in for building footprints
    w, s, e, n = p["w"], p["s"], p["e"], p["n"]
    with pool.connection() as conn, conn.cursor() as cur:
        return _feature_collection(cur, """
            SELECT json_build_object('type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 6)::json,
                'properties', json_build_object('id', id, 'usage', usage_label, 'storeys', storeys_above,
                    'height_m', height_m, 'flood_depth_m', flood_max_depth_m))
            FROM koko.buildings
            WHERE geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
            -- when the cap is hit, keep the buildings nearest the view centre (no holes in the middle)
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint((%(w)s + %(e)s) / 2, (%(s)s + %(n)s) / 2), 4326)
            LIMIT %(limit)s""", {"w": w, "s": s, "e": e, "n": n, "limit": limit})


@app.get("/report")
def report(lat: float = Query(..., ge=20, le=46), lon: float = Query(..., ge=122, le=154),
           earthquake: bool = Query(True, description="include the live J-SHIS lookup")):
    with pool.connection() as conn:
        try:
            return build_report(conn, lat, lon, with_earthquake=earthquake)
        except OutsideCoverage as e:
            raise HTTPException(status_code=422, detail=str(e))


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class Selection(BaseModel):
    lat: float = Field(ge=20, le=46)
    lon: float = Field(ge=122, le=154)
    label: str | None = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=40)
    selection: Selection | None = None
    lang: Literal["en", "ja"] = "en"


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    lang: Literal["en", "ja"]


@app.post("/chat/translate")
async def chat_translate(req: TranslateRequest):
    """Translate a finished Ask KOKO answer when the user switches the interface language."""
    try:
        return {"text": await assistant.translate(req.text, req.lang)}
    except assistant.TranslateError as e:
        raise HTTPException(status_code=502 if e.code != "no_key" else 503, detail={"code": e.code})


@app.get("/chat/status")
def chat_status():
    return {"configured": assistant.is_configured(), "model": assistant.MODEL}


@app.post("/chat")
async def chat(req: ChatRequest):
    """Ask KOKO: streams Server-Sent Events (text deltas, tool activity, done / error)."""
    if req.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="last message must be from the user")
    events = assistant.chat_stream(pool, [m.model_dump() for m in req.messages],
                                   req.selection.model_dump() if req.selection else None, req.lang)
    return StreamingResponse(events, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# The dashboard (plain HTML + Leaflet). Mounted last so the API routes above take priority.
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
