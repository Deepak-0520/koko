"""Stage 国土数値情報 (KSJ) layers, one prefecture or one flood mesh at a time."""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from . import config
from .download import fetch, find_members, vsizip

log = logging.getLogger(__name__)
CRS = "EPSG:4326"


def _read_member(zip_path: Path, raw: str, name: str) -> gpd.GeoDataFrame:
    kw = {"use_arrow": True}
    if name.endswith(".geojson"):
        kw["encoding"] = "utf-8"
    else:  # shapefiles: trust a .cpg if present, otherwise KSJ's Shift_JIS
        with zipfile.ZipFile(zip_path) as z:
            has_cpg = any(n.lower().endswith(".cpg") for n in z.namelist())
        if not has_cpg:
            kw["encoding"] = "cp932"
    gdf = pyogrio.read_dataframe(vsizip(zip_path, raw), **kw)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf.to_crs(CRS)


def read_layer(zip_path: Path, contains: str = "") -> gpd.GeoDataFrame:
    """Read the single data layer of a KSJ zip, preferring GeoJSON over Shapefile."""
    for suffix in (".geojson", ".shp"):
        members = [m for m in find_members(zip_path, suffix=suffix, contains=contains) if "KS-META" not in m[1]]
        if members:
            frames = [_read_member(zip_path, raw, name) for raw, name in members]
            return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS)
    raise FileNotFoundError(f"no GeoJSON/Shapefile layer in {zip_path.name}")


def stage_municipalities(pref: str) -> gpd.GeoDataFrame:
    z = fetch(config.DATASETS["municipalities"]["url"].format(pref=pref), config.RAW_DIR)
    gdf = read_layer(z)
    gdf = gdf[gdf["N03_007"].notna() & ~gdf["N03_007"].astype(str).str.endswith("000")]
    gdf = gdf.dissolve(by="N03_007", as_index=False, aggfunc="first")
    pref_ja, pref_en = config.PREFECTURES[pref]
    out = gpd.GeoDataFrame({
        "code": gdf["N03_007"],
        "pref_code": pref,
        "pref_ja": pref_ja,
        "pref_en": pref_en,
        "city_ja": gdf["N03_004"],
        "ward_ja": gdf["N03_005"],
        "name_ja": (gdf["N03_004"].fillna("") + gdf["N03_005"].fillna("")).replace("", None),
    }, geometry=gdf.geometry, crs=CRS)
    return out


def stage_flood_mesh(mesh: str, cls: str, url: str) -> dict[str, gpd.GeoDataFrame]:
    """A31b for one 1st-order mesh and river class: depth (L1 / L2), duration, house-collapse zones."""
    river_class = {"10": "designated", "20": "other"}[cls]
    z = fetch(url, config.RAW_DIR)
    depth, duration, collapse = [], [], []
    for raw, name in find_members(z, suffix=".geojson"):
        folder = re.split(r"[/\\]", name)[0]
        gdf = _read_member(z, raw, name)
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
        if not frames:
            return gpd.GeoDataFrame(columns=cols + ["geometry"], geometry="geometry", crs=CRS)
        return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS)[cols + ["geometry"]]

    return {
        "flood_depth": cat(depth, ["scale", "river_class", "depth_rank"]),
        "flood_duration": cat(duration, ["river_class", "duration_rank"]),
        "flood_collapse": cat(collapse, ["river_class", "kind"]),
    }


def stage_landslide(pref: str) -> gpd.GeoDataFrame:
    z = fetch(config.DATASETS["landslide"]["url"].format(pref=pref), config.RAW_DIR)
    gdf = read_layer(z, contains="Polygon")
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


def stage_depth_zones(url: str, label_col: str) -> gpd.GeoDataFrame:
    """Tsunami (A40_003) and storm surge (A49_003) inundation depth zones."""
    gdf = read_layer(fetch(url, config.RAW_DIR))
    gdf = gdf.rename(columns={label_col: "depth_label"})
    parsed = gdf["depth_label"].map(parse_depth_label)
    gdf["depth_min_m"] = parsed.map(lambda t: t[0])
    gdf["depth_max_m"] = parsed.map(lambda t: t[1])
    return gdf[["depth_label", "depth_min_m", "depth_max_m", "geometry"]]
