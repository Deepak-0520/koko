"""Load staged GeoParquet files into PostGIS with COPY."""
from __future__ import annotations

import json
import logging
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

# staged file stem -> (table, source key for attribution)
TABLES = {
    "wards": ("koko.wards", "wards"),
    "flood_depth": ("koko.flood_depth", "flood_designated"),
    "flood_duration": ("koko.flood_duration", "flood_designated"),
    "flood_collapse": ("koko.flood_collapse", "flood_designated"),
    "landslide": ("koko.landslide", "landslide"),
    "tsunami": ("koko.tsunami", "tsunami"),
    "storm_surge": ("koko.storm_surge", "storm_surge"),
    "shelters": ("koko.shelters", "shelters"),
}


def run_sql(conn: psycopg.Connection, name: str) -> None:
    conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def _to_db_value(v):
    if v is pd.NA:
        return None
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, np.ndarray):
        return json.dumps(v.tolist(), ensure_ascii=False)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def copy_gdf(conn: psycopg.Connection, gdf: gpd.GeoDataFrame, table: str, *, multi: bool = False) -> int:
    geoms = gdf.geometry
    if multi:
        geoms = geoms.map(lambda g: MultiPolygon([g]) if isinstance(g, Polygon) else g)
    # hex EWKB with SRID 4326 is accepted directly by COPY into a geometry column
    ewkb = shapely.to_wkb(shapely.set_srid(np.asarray(geoms), 4326), hex=True, include_srid=True)
    cols = [c for c in gdf.columns if c != gdf.geometry.name]
    sql = f"COPY {table} ({', '.join(cols + ['geom'])}) FROM STDIN"
    with conn.cursor() as cur, cur.copy(sql) as cp:
        for values, g in zip(gdf[cols].itertuples(index=False, name=None), ewkb):
            cp.write_row([_to_db_value(v) for v in values] + [g])
    return len(gdf)


def _record(conn, source: str, rows: int, title: str | None = None, url: str | None = None) -> None:
    src = config.SOURCES.get(source, {})
    conn.execute(
        """INSERT INTO koko.source_meta (source, title, url, rows) VALUES (%s, %s, %s, %s)
           ON CONFLICT (source) DO UPDATE SET title = EXCLUDED.title, url = EXCLUDED.url,
               rows = EXCLUDED.rows, loaded_at = now()""",
        (source, title or src.get("title", source), url or src.get("url", ""), rows),
    )


def load_all(staged_dir: Path | None = None, database_url: str | None = None) -> None:
    staged_dir = staged_dir or config.STAGED_DIR
    with psycopg.connect(database_url or config.DATABASE_URL, autocommit=False) as conn:
        run_sql(conn, "001_schema.sql")
        for stem, (table, source) in TABLES.items():
            path = staged_dir / f"{stem}.parquet"
            if not path.exists():
                log.warning("skip %s (not staged)", stem)
                continue
            gdf = gpd.read_parquet(path)
            conn.execute(f"TRUNCATE {table} RESTART IDENTITY")
            n = copy_gdf(conn, gdf, table, multi=(stem == "wards"))
            _record(conn, stem, n, title=config.SOURCES[source]["title"], url=config.SOURCES[source]["url"])
            log.info("loaded %s: %d rows", table, n)

        ward_files = sorted((staged_dir / "plateau").glob("*.parquet"))
        if ward_files:
            for path in ward_files:
                gdf = gpd.read_parquet(path).astype({"storeys_above": "Int16", "storeys_below": "Int16"})
                conn.execute("DELETE FROM koko.buildings WHERE ward_code = %s", (path.stem,))
                n = copy_gdf(conn, gdf, "koko.buildings")
                log.info("loaded buildings %s: %d rows", path.stem, n)
            total = conn.execute("SELECT count(*) FROM koko.buildings").fetchone()[0]
            _record(conn, "plateau", total)

        run_sql(conn, "002_indexes.sql")
        conn.commit()
