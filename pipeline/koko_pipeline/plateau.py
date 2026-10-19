"""Stage PLATEAU buildings (footprint, height, storeys, per-building disaster risk) per ward.

Each ward's CityGML zip is 0.7–2.5 GB, mostly textures and other feature types. We read
only udx/bldg/*.gml and codelists/*.xml with HTTP range requests (remotezip), which cuts
the download roughly 10x.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import requests
from lxml import etree
from remotezip import RemoteZip
from shapely.geometry import MultiPolygon, Polygon

from . import config

log = logging.getLogger(__name__)

NS = {
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "xAL": "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0",
}
BUILDING_TAG = f"{{{NS['bldg']}}}Building"


@lru_cache(maxsize=1)
def _catalog() -> list[dict]:
    return requests.get(config.DATASETS["buildings"]["url"], timeout=120).json()["citygml"]


def all_city_codes() -> list[str]:
    """Every city with PLATEAU building data, smallest download first (quick wins before the huge ones)."""
    latest: dict[str, dict] = {}
    for r in _catalog():
        if "bldg" in (r.get("feature_types") or []):
            if r["city_code"] not in latest or r["year"] > latest[r["city_code"]]["year"]:
                latest[r["city_code"]] = r
    return [c for c, _ in sorted(latest.items(), key=lambda kv: kv[1]["file_size"])]


def catalog_entry(city_code: str) -> dict:
    """Latest CityGML entry for a city (or Tokyo ward) from the PLATEAU data catalog."""
    rows = [r for r in _catalog() if r["city_code"] == city_code]
    if not rows:
        raise LookupError(f"no PLATEAU CityGML for {city_code}")
    return max(rows, key=lambda r: r["year"])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_codelists(z: RemoteZip) -> dict[str, dict[str, str]]:
    """codelists/Foo_bar.xml -> {'Foo_bar': {code: label}}"""
    out = {}
    for name in z.namelist():
        if "codelists/" in name and name.endswith(".xml"):
            text = z.read(name).decode("utf-8", "replace")
            pairs = re.findall(r"<gml:description>(.*?)</gml:description>\s*<gml:name>(.*?)</gml:name>", text, re.S)
            out[Path(name).stem] = {code.strip(): label.strip() for label, code in pairs}
    return out


def _poslist_to_ring(text: str) -> list[tuple[float, float]]:
    v = [float(x) for x in text.split()]
    # EPSG:6697 is lat, lon, height -> shapely wants lon, lat
    return [(v[i + 1], v[i]) for i in range(0, len(v), 3)]


def _polygons(elem, paths: list[str]) -> list[Polygon]:
    for path in paths:
        polys = []
        for ext in elem.iterfind(path, NS):
            ring = _poslist_to_ring(ext.text)
            if len(ring) >= 4:
                polys.append(Polygon(ring))
        if polys:
            return polys
    return []


def _footprint(bld) -> MultiPolygon | Polygon | None:
    polys = _polygons(bld, [
        ".//bldg:lod0FootPrint//gml:exterior//gml:posList",
        ".//bldg:lod0RoofEdge//gml:exterior//gml:posList",
    ])
    if not polys:  # fall back to the lowest face of the LOD1 solid
        faces = []
        for pl in bld.iterfind(".//bldg:lod1Solid//gml:exterior//gml:LinearRing/gml:posList", NS):
            v = [float(x) for x in pl.text.split()]
            zs = v[2::3]
            faces.append((sum(zs) / len(zs), Polygon([(v[i + 1], v[i]) for i in range(0, len(v), 3)])))
        if faces:
            polys = [min(faces, key=lambda f: f[0])[1]]
    if not polys:
        return None
    polys = [p if p.is_valid else p.buffer(0) for p in polys]
    return polys[0] if len(polys) == 1 else MultiPolygon([g for p in polys for g in getattr(p, "geoms", [p])])


def _num(bld, path: str, cast=float):
    """Numeric attribute; PLATEAU encodes 'unknown' as 9999 / -9999, which becomes None."""
    el = bld.find(path, NS)
    try:
        v = cast(el.text) if el is not None and el.text else None
    except ValueError:
        return None
    return None if v is not None and abs(v) >= 9999 else v


def parse_building(bld, codelists: dict[str, dict[str, str]]) -> dict | None:
    geom = _footprint(bld)
    if geom is None or geom.is_empty:
        return None

    risks = []
    for el in bld.iter():
        if not isinstance(el.tag, str):
            continue
        rtype = _local(el.tag)
        if not rtype.endswith("RiskAttribute") or el.getparent() is None or _local(el.getparent().tag) != "bldgDisasterRiskAttribute":
            continue
        r = {"type": rtype}
        for child in el:
            key = _local(child.tag)
            r[key] = child.text.strip() if child.text else None
            labels = codelists.get(f"{rtype}_{key}")
            if labels and r[key] in labels:
                r[f"{key}_label"] = labels[r[key]]
        risks.append(r)

    flood_l2 = [float(r["depth"]) for r in risks
                if r["type"] == "RiverFloodingRiskAttribute" and r.get("scale") == "2" and r.get("depth")]

    bid = bld.find(".//{*}buildingID")
    usage = bld.findtext("bldg:usage", namespaces=NS)
    return {
        "id": bid.text if bid is not None else bld.get(f"{{{NS['gml']}}}id"),
        "city_code": bld.findtext(".//{*}BuildingIDAttribute/{*}city"),
        "usage_code": usage,
        "usage_label": codelists.get("Building_usage", {}).get(usage),
        "height_m": _num(bld, "bldg:measuredHeight"),
        "storeys_above": _num(bld, "bldg:storeysAboveGround", int),
        "storeys_below": _num(bld, "bldg:storeysBelowGround", int),
        "address": bld.findtext(".//xAL:LocalityName", namespaces=NS),
        "flood_max_depth_m": max(flood_l2) if flood_l2 else None,
        "risks": risks,
        "geometry": geom,
    }


def iter_buildings(stream, codelists):
    for _, el in etree.iterparse(stream, events=("end",), tag=BUILDING_TAG, huge_tree=True):
        # skip BuildingParts nested in a Building: only top-level Buildings are emitted
        row = parse_building(el, codelists)
        if row:
            yield row
        el.clear()
        parent = el.getparent()
        while parent is not None and el.getprevious() is not None:
            del parent[0]


def stage_city(city_code: str, own_codes: set[str]) -> gpd.GeoDataFrame:
    """Buildings of one PLATEAU dataset.

    own_codes is the set of municipality codes this dataset owns: the city itself, plus its wards
    for a designated city (Osaka 27100 owns 27102–27128). Mesh files on the edge also contain the
    neighbouring city's buildings; those belong to that city's dataset and are dropped here.
    """
    url = catalog_entry(city_code)["url"]
    log.info("PLATEAU %s: %s", city_code, url)
    rows = []
    with RemoteZip(url) as z:
        codelists = parse_codelists(z)
        members = [n for n in z.namelist() if re.search(r"udx/bldg/[^/]+\.gml$", n)]
        for i, name in enumerate(members, 1):
            with z.open(name) as f:
                before = len(rows)
                rows.extend(iter_buildings(f, codelists))
            log.info("  [%d/%d] %s: %d buildings", i, len(members), Path(name).name, len(rows) - before)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:6668").to_crs("EPSG:4326")
    gdf["city_code"] = gdf["city_code"].fillna(gdf["id"].str[:5])
    parsed = len(gdf)
    gdf = gdf[gdf["city_code"].isin(own_codes | {city_code})].drop_duplicates("id")
    log.info("PLATEAU %s: kept %d of %d parsed buildings (others belong to neighbouring datasets)",
             city_code, len(gdf), parsed)
    if parsed and not len(gdf):
        log.warning("PLATEAU %s: every building was dropped; building city codes seen: %s",
                    city_code, sorted(set(gpd.GeoDataFrame(rows)["city_code"].dropna()))[:20])
    return gdf.astype({"storeys_above": "Int16", "storeys_below": "Int16"})
