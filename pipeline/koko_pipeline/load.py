"""Load staged GeoParquet files into PostGIS with COPY, one file (= one `src`) at a time.

Staged layout: data/staged/<layer>/<src>.parquet. A file is skipped when the same version
(by modification time) is already loaded, so an interrupted national load can simply be rerun.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import psycopg
import shapely
from shapely.geometry import MultiPolygon, Polygon

from . import config

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"

# layer -> (table, dataset key in config.DATASETS)
LAYERS = {
    "municipalities": ("koko.municipalities", "municipalities"),
    "flood_depth": ("koko.flood_depth", "flood"),
    "flood_duration": ("koko.flood_duration", "flood"),
    "flood_collapse": ("koko.flood_collapse", "flood"),
    "landslide": ("koko.landslide", "landslide"),
    "tsunami": ("koko.tsunami", "tsunami"),
    "storm_surge": ("koko.storm_surge", "storm_surge"),
    "shelters": ("koko.shelters", "shelters"),
    "buildings": ("koko.buildings", "buildings"),
}
SUBDIVIDE = {"flood_depth", "flood_duration", "flood_collapse", "landslide", "tsunami", "storm_surge"}
MAX_VERTICES = 256


def run_sql(conn: psycopg.Connection, name: str) -> None:
    conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def _to_db_value(v):
    if v is pd.NA or v is None:
        return None
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, np.ndarray):
        return json.dumps(v.tolist(), ensure_ascii=False)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) else float(v)
    return v


def copy_gdf(conn: psycopg.Connection, gdf: gpd.GeoDataFrame, table: str, *, multi: bool = False,
             chunk: int = 200_000) -> int:
    """COPY a GeoDataFrame as CSV, built by pandas in chunks (much faster than row-by-row)."""
    geoms = gdf.geometry
    if multi:
        geoms = geoms.map(lambda g: MultiPolygon([g]) if isinstance(g, Polygon) else g)
    cols = [c for c in gdf.columns if c != gdf.geometry.name]
    df = pd.DataFrame(gdf[cols])
    for c in cols:  # JSON columns (e.g. PLATEAU risks) go in as text
        if df[c].dtype == object and df[c].map(lambda v: isinstance(v, (list, dict, np.ndarray))).any():
            df[c] = df[c].map(_to_db_value)
    # hex EWKB with SRID 4326 is accepted directly by COPY into a geometry column
    df["geom"] = shapely.to_wkb(shapely.set_srid(np.asarray(geoms), 4326), hex=True, include_srid=True)
    sql = f"COPY {table} ({', '.join(cols + ['geom'])}) FROM STDIN WITH (FORMAT csv)"
    with conn.cursor() as cur, cur.copy(sql) as cp:
        for start in range(0, len(df), chunk):
            # unquoted empty fields are NULL in CSV COPY; real empty strings are written quoted
            cp.write(df.iloc[start:start + chunk].to_csv(header=False, index=False))
    return len(df)


def _subdivide(conn, table: str, src: str) -> None:
    """Split polygons with more than MAX_VERTICES vertices so point-in-polygon stays cheap."""
    cols = [r[0] for r in conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'koko' AND table_name = %s AND column_name <> 'geom' ORDER BY ordinal_position""",
        (table.split(".")[1],)).fetchall()]
    c = ", ".join(cols)
    conn.execute(f"""
        WITH big AS (DELETE FROM {table} WHERE src = %(src)s AND ST_NPoints(geom) > {MAX_VERTICES} RETURNING {c}, geom)
        INSERT INTO {table} ({c}, geom) SELECT {c}, ST_Subdivide(geom, {MAX_VERTICES}) FROM big""", {"src": src})


def _prepare(layer: str, gdf: gpd.GeoDataFrame, src: str) -> gpd.GeoDataFrame:
    if layer == "municipalities":
        return gdf
    if layer == "buildings":
        if "ward_code" in gdf.columns:  # files staged before the nationwide pipeline
            gdf = gdf.rename(columns={"ward_code": "city_code"})
        gdf = gdf.astype({"storeys_above": "Int16", "storeys_below": "Int16"})
    gdf = gdf.copy()
    gdf.insert(0, "src", src)
    return gdf


def _load_file(conn, layer: str, path: Path) -> int:
    table = LAYERS[layer][0]
    src = path.stem
    gdf = _prepare(layer, gpd.read_parquet(path), src)
    if layer == "municipalities":
        conn.execute("DELETE FROM koko.municipality_parts WHERE code IN "
                     "(SELECT code FROM koko.municipalities WHERE pref_code = %s)", (src,))
        conn.execute("DELETE FROM koko.municipalities WHERE pref_code = %s", (src,))
        n = copy_gdf(conn, gdf, table, multi=True)
        conn.execute(f"""INSERT INTO koko.municipality_parts (code, geom)
                         SELECT code, ST_Subdivide(geom, {MAX_VERTICES}) FROM koko.municipalities
                         WHERE pref_code = %s""", (src,))
    elif layer == "buildings":
        # PLATEAU building IDs are unique per city, but two datasets can share a border building:
        # go through a staging table and skip IDs that another dataset already loaded
        conn.execute("DELETE FROM koko.buildings WHERE src = %s", (src,))
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS buildings_in (LIKE koko.buildings) ON COMMIT DELETE ROWS")
        copy_gdf(conn, gdf, "buildings_in") if len(gdf) else 0
        n = conn.execute("INSERT INTO koko.buildings SELECT * FROM buildings_in ON CONFLICT (id) DO NOTHING").rowcount
        if n < len(gdf):
            log.warning("buildings %s: skipped %d duplicate IDs already loaded from another dataset", src, len(gdf) - n)
    else:
        conn.execute(f"DELETE FROM {table} WHERE src = %s", (src,))
        n = copy_gdf(conn, gdf, table) if len(gdf) else 0
        if layer in SUBDIVIDE:
            _subdivide(conn, table, src)
    conn.execute(
        """INSERT INTO koko.loaded_files (layer, src, file_mtime, rows) VALUES (%s, %s, %s, %s)
           ON CONFLICT (layer, src) DO UPDATE SET file_mtime = EXCLUDED.file_mtime,
               rows = EXCLUDED.rows, loaded_at = now()""",
        (layer, src, path.stat().st_mtime, n))
    return n


def load_all(layers: list[str] | None = None, *, reset: bool = False, force: bool = False,
             database_url: str | None = None, staged_dir: Path | None = None) -> None:
    staged_dir = staged_dir or config.STAGED_DIR
    with psycopg.connect(database_url or config.DATABASE_URL) as conn:
        if reset:
            run_sql(conn, "000_reset.sql")
        run_sql(conn, "001_schema.sql")
        for key, d in config.DATASETS.items():
            conn.execute("""INSERT INTO koko.source_meta (source, title, url) VALUES (%s, %s, %s)
                            ON CONFLICT (source) DO UPDATE SET title = EXCLUDED.title, url = EXCLUDED.url""",
                         (key, d["title"], d["page"]))
        conn.commit()

        loaded = {(r[0], r[1]): r[2] for r in conn.execute("SELECT layer, src, file_mtime FROM koko.loaded_files")}
        for layer in layers or LAYERS:
            dirs = [staged_dir / layer] + ([staged_dir / "plateau"] if layer == "buildings" else [])
            files = sorted(p for d in dirs if d.is_dir() for p in d.glob("*.parquet"))
            # files touched in the last minute may still be being written by a running stage job
            settled = [p for p in files if time.time() - p.stat().st_mtime > 60]
            todo = [p for p in settled if force or loaded.get((layer, p.stem)) != p.stat().st_mtime]
            log.info("%s: %d staged files, %d to load", layer, len(files), len(todo))
            for i, path in enumerate(todo, 1):
                n = _load_file(conn, layer, path)
                conn.commit()  # per file, so an interrupted run keeps its progress
                log.info("  [%d/%d] %s %s: %d rows", i, len(todo), layer, path.stem, n)
            if todo:
                conn.execute(f"ANALYZE {LAYERS[layer][0]}")
                conn.commit()
