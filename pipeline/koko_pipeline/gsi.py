"""Stage GSI designated emergency evacuation sites (指定緊急避難場所) from the skhb0N GeoJSON tiles."""
from __future__ import annotations

import logging
import math

import geopandas as gpd
import requests
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from . import config
from .download import USER_AGENT

log = logging.getLogger(__name__)
ZOOM = 10  # the skhb tile sets are published at z10


def _tile_range(bbox: tuple[float, float, float, float], z: int):
    lon0, lat0, lon1, lat1 = bbox
    n = 2**z

    def tx(lon):
        return int((lon + 180) / 360 * n)

    def ty(lat):
        return int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)

    for x in range(tx(lon0), tx(lon1) + 1):
        for y in range(ty(lat1), ty(lat0) + 1):
            yield x, y


def stage_shelters(area: BaseGeometry) -> gpd.GeoDataFrame:
    tpl = config.SOURCES["shelters"]["url"]
    sites: dict[tuple, dict] = {}
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for n, dtype in config.SHELTER_TYPES.items():
        for x, y in _tile_range(config.TOKYO_23_BBOX, ZOOM):
            url = tpl.format(n=n, z=ZOOM, x=x, y=y)
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            for f in r.json()["features"]:
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
        log.info("shelters: %s done, %d unique sites so far", dtype, len(sites))
    gdf = gpd.GeoDataFrame(list(sites.values()), geometry="geometry", crs="EPSG:4326")
    return gdf.iloc[sorted(gdf.sindex.query(area, predicate="intersects"))].reset_index(drop=True)
