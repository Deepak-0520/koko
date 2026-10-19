"""Stage 国土数値情報 (KSJ) hazard datasets for the Tokyo 23 wards."""
from __future__ import annotations

import logging
import re

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry.base import BaseGeometry

from . import config
from .download import fetch, find_members, vsizip

log = logging.getLogger(__name__)
CRS = "EPSG:4326"


def _read(path: str, **kw) -> gpd.GeoDataFrame:
    gdf = pyogrio.read_dataframe(path, bbox=config.TOKYO_23_BBOX, use_arrow=True, encoding="utf-8", **kw)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf.to_crs(CRS)


def _clip_to(gdf: gpd.GeoDataFrame, area: BaseGeometry) -> gpd.GeoDataFrame:
    idx = gdf.sindex.query(area, predicate="intersects")
    return gdf.iloc[sorted(idx)].reset_index(drop=True)


def stage_wards() -> gpd.GeoDataFrame:
    z = fetch(config.SOURCES["wards"]["url"], config.RAW_DIR)
    (raw, _), = find_members(z, suffix=".geojson")
    gdf = _read(vsizip(z, raw))
    gdf = gdf[gdf["N03_007"].isin(config.TOKYO_23)]
    gdf = gdf.dissolve(by="N03_007", as_index=False)[["N03_007", "geometry"]]
    gdf = gdf.rename(columns={"N03_007": "code"})
    gdf["name_ja"] = gdf["code"].map(lambda c: config.TOKYO_23[c][0])
    gdf["name_en"] = gdf["code"].map(lambda c: config.TOKYO_23[c][1])
    return gdf[["code", "name_ja", "name_en", "geometry"]]


def stage_flood(area: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    """A31b: depth (planned L1 / max L2), duration, and house-collapse zones, for both river classes."""
    depth, duration, collapse = [], [], []
    for key, river_class in (("flood_designated", "designated"), ("flood_other", "other")):
        z = fetch(config.SOURCES[key]["url"], config.RAW_DIR)
        for raw, name in find_members(z, suffix=".geojson"):
            folder = name.split("/")[0].split("\\")[0]
            log.info("reading %s %s", key, folder)
            gdf = _clip_to(_read(vsizip(z, raw)), area)
            gdf["river_class"] = river_class
            if folder.startswith("10_"):
                depth.append(gdf.assign(scale="L1", depth_rank=gdf["A31b_101"]))
            elif folder.startswith("20_"):
                depth.append(gdf.assign(scale="L2", depth_rank=gdf["A31b_201"]))
            elif folder.startswith("30_"):
                duration.append(gdf.assign(duration_rank=gdf["A31b_301"]))
            elif folder.startswith(("41_", "42_")):
                collapse.append(gdf.assign(kind=gdf["A31b_401"]))

    def cat(frames, cols):
        out = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS)
        return out[cols + ["geometry"]]

    return {
        "flood_depth": cat(depth, ["scale", "river_class", "depth_rank"]),
        "flood_duration": cat(duration, ["river_class", "duration_rank"]),
        "flood_collapse": cat(collapse, ["river_class", "kind"]),
    }


def stage_landslide(area: BaseGeometry) -> gpd.GeoDataFrame:
    z = fetch(config.SOURCES["landslide"]["url"], config.RAW_DIR)
    (raw, _), = find_members(z, suffix="Polygon.geojson")
    gdf = _clip_to(_read(vsizip(z, raw)), area)
    gdf = gdf.rename(columns={"A33_001": "phenomenon", "A33_002": "zone", "A33_005": "name", "A33_006": "location"})
    return gdf[["phenomenon", "zone", "name", "location", "geometry"]]


_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def parse_depth_label(label: str | None) -> tuple[float | None, float | None]:
    """'0.3m以上～1.0m未満' -> (0.3, 1.0); '～0.3m未満' -> (0, 0.3); '20m以上' -> (20, None)."""
    if not label:
        return None, None
    nums = [float(n) for n in _NUM.findall(label)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return (nums[0], None) if "以上" in label and "未満" not in label else (0.0, nums[0])
    return None, None


def _stage_depth_zones(key: str, label_col: str, area: BaseGeometry) -> gpd.GeoDataFrame:
    z = fetch(config.SOURCES[key]["url"], config.RAW_DIR)
    (raw, _), = [m for m in find_members(z, suffix=".geojson") if "KS-META" not in m[1]]
    gdf = _clip_to(_read(vsizip(z, raw)), area)
    gdf = gdf.rename(columns={label_col: "depth_label"})
    parsed = gdf["depth_label"].map(parse_depth_label)
    gdf["depth_min_m"] = parsed.map(lambda t: t[0])
    gdf["depth_max_m"] = parsed.map(lambda t: t[1])
    return gdf[["depth_label", "depth_min_m", "depth_max_m", "geometry"]]


def stage_tsunami(area: BaseGeometry) -> gpd.GeoDataFrame:
    return _stage_depth_zones("tsunami", "A40_003", area)


def stage_storm_surge(area: BaseGeometry) -> gpd.GeoDataFrame:
    return _stage_depth_zones("storm_surge", "A49_003", area)
