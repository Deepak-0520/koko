"""Stage GSI designated emergency evacuation sites (指定緊急避難場所) per prefecture from the skhb0N tiles."""
from __future__ import annotations

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import requests
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from . import config
from .download import USER_AGENT

log = logging.getLogger(__name__)
ZOOM = 10  # the skhb tile sets are published at z10
TILE_CACHE = config.RAW_DIR / "gsi_skhb"
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def _tile_bounds(x: int, y: int, z: int):
    n = 2**z

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return box(x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y))


def tiles_covering(area: BaseGeometry, z: int = ZOOM) -> list[tuple[int, int]]:
    lon0, lat0, lon1, lat1 = area.bounds
    n = 2**z

    def tx(lon):
        return int((lon + 180) / 360 * n)

    def ty(lat):
        return int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)

    return [(x, y) for x in range(tx(lon0), tx(lon1) + 1) for y in range(ty(lat1), ty(lat0) + 1)
            if _tile_bounds(x, y, z).intersects(area)]


def _get_tile(n: int, x: int, y: int) -> list[dict]:
    """Fetch one tile, cached on disk (an empty list is cached for 404s)."""
    path = TILE_CACHE / f"{n}" / f"{ZOOM}_{x}_{y}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = config.DATASETS["shelters"]["url"].format(n=n, z=ZOOM, x=x, y=y)
    r = _session.get(url, timeout=60)
    if r.status_code == 404:
        features = []
    else:
        r.raise_for_status()
        features = r.json()["features"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
    return features


def stage_shelters(pref: str, area: BaseGeometry) -> gpd.GeoDataFrame:
    """Sites inside one prefecture. Tiles straddle borders, so each site is kept by the prefecture
    named at the start of its address (falling back to location), which avoids duplicates."""
    tiles = tiles_covering(area)
    jobs = [(n, x, y) for n in config.SHELTER_TYPES for x, y in tiles]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda j: (j[0], _get_tile(*j)), jobs))

    sites: dict[tuple, dict] = {}
    for n, features in results:
        dtype = config.SHELTER_TYPES[n]
        for f in features:
            lon, lat = f["geometry"]["coordinates"][:2]
            p = f["properties"]
            key = (p.get("name"), round(lon, 6), round(lat, 6))
            site = sites.setdefault(key, {
                "name": p.get("name"),
                "address": p.get("address"),
                "remarks": p.get("remarks") or None,
                "geometry": Point(lon, lat),
                **{t: False for t in config.SHELTER_TYPES.values()},
            })
            site[dtype] = True
    if not sites:
        return gpd.GeoDataFrame(columns=["name", "address", "remarks", *config.SHELTER_TYPES.values(), "geometry"],
                                geometry="geometry", crs="EPSG:4326")

    gdf = gpd.GeoDataFrame(list(sites.values()), geometry="geometry", crs="EPSG:4326")
    pref_ja = config.PREFECTURES[pref][0]
    addr = gdf["address"].fillna("")
    other_pref = addr.str.match("|".join(v[0] for k, v in config.PREFECTURES.items() if k != pref))
    keep = addr.str.startswith(pref_ja) | (~other_pref & gdf.within(area.buffer(0.01)))
    log.info("shelters %s: %d tiles, %d sites kept of %d", pref, len(tiles), int(keep.sum()), len(gdf))
    return gdf[keep].reset_index(drop=True)
