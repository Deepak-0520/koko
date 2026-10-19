"""KOKO data pipeline.

    python -m koko_pipeline stage --prefs 13 27          # hazard layers for Tokyo and Osaka
    python -m koko_pipeline stage --prefs all            # all 47 prefectures
    python -m koko_pipeline stage --prefs 27 --only landslide shelters
    python -m koko_pipeline plateau --cities major       # Tokyo 23 wards + major cities
    python -m koko_pipeline plateau --cities all         # every PLATEAU city (~300; 1-2 days)
    python -m koko_pipeline plateau --cities 27100 14100
    python -m koko_pipeline load                         # staged files -> PostGIS (incremental)
    python -m koko_pipeline load --reset                 # drop and rebuild the koko schema first
"""
from __future__ import annotations

import argparse
import logging
import time
import traceback

import geopandas as gpd
from shapely.geometry import box

from . import catalog, config, gsi, ksj, load, plateau

log = logging.getLogger("koko_pipeline")
STAGES = ["municipalities", "landslide", "tsunami", "storm_surge", "shelters", "flood"]


def _path(layer: str, src: str):
    return config.STAGED_DIR / layer / f"{src}.parquet"


def _save(gdf: gpd.GeoDataFrame, layer: str, src: str) -> None:
    path = _path(layer, src)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")  # write then rename, so a loader never sees half a file
    gdf.to_parquet(tmp)
    tmp.replace(path)
    log.info("staged %s/%s: %d rows", layer, src, len(gdf))


def _pref_area(pref: str):
    """Simplified outline of a prefecture (from staged municipalities) for tile and mesh selection."""
    path = _path("municipalities", pref)
    if not path.exists():
        _save(ksj.stage_municipalities(pref), "municipalities", pref)
    return gpd.read_parquet(path).geometry.simplify(0.005).union_all()


def first_meshes(area) -> set[str]:
    """JIS 1st-order mesh codes (40' lat x 1° lon) intersecting an area."""
    lon0, lat0, lon1, lat1 = area.bounds
    out = set()
    for a in range(int(lat0 * 1.5), int(lat1 * 1.5) + 1):
        for b in range(int(lon0) - 100, int(lon1) - 100 + 1):
            cell = box(b + 100, a / 1.5, b + 101, (a + 1) / 1.5)
            if cell.intersects(area):
                out.add(f"{a:02d}{b:02d}")
    return out


class Runner:
    """Runs stage items, skipping already-staged ones and collecting failures instead of stopping."""

    def __init__(self, force: bool):
        self.force = force
        self.failed: list[str] = []

    def done(self, layer: str, src: str) -> bool:
        return not self.force and _path(layer, src).exists()

    def run(self, label: str, fn) -> None:
        t = time.perf_counter()
        try:
            fn()
            log.info("%s done in %.0fs", label, time.perf_counter() - t)
        except Exception as e:  # keep going: one broken file should not stop a national run
            self.failed.append(f"{label}: {e}")
            log.error("%s FAILED: %s\n%s", label, e, traceback.format_exc(limit=3))


def cmd_stage(args) -> None:
    prefs = sorted(config.PREFECTURES) if args.prefs == ["all"] else args.prefs
    unknown = set(prefs) - set(config.PREFECTURES)
    if unknown:
        raise SystemExit(f"unknown prefecture codes: {sorted(unknown)}")
    stages = args.only or STAGES
    r = Runner(args.force)
    tsunami = catalog.latest_by_pref("tsunami") if "tsunami" in stages else {}
    surge = catalog.latest_by_pref("storm_surge") if "storm_surge" in stages else {}

    meshes: set[str] = set()
    for pref in prefs:
        name = f"{pref} {config.PREFECTURES[pref][1]}"
        if not r.done("municipalities", pref):
            r.run(f"municipalities {name}", lambda: _save(ksj.stage_municipalities(pref), "municipalities", pref))
        if "landslide" in stages and not r.done("landslide", pref):
            r.run(f"landslide {name}", lambda: _save(ksj.stage_landslide(pref), "landslide", pref))
        if pref in tsunami and not r.done("tsunami", pref):
            r.run(f"tsunami {name}", lambda: _save(ksj.stage_depth_zones(tsunami[pref], "A40_003"), "tsunami", pref))
        if pref in surge and not r.done("storm_surge", pref):
            r.run(f"storm surge {name}", lambda: _save(ksj.stage_depth_zones(surge[pref], "A49_003"), "storm_surge", pref))
        if "shelters" in stages and not r.done("shelters", pref):
            r.run(f"shelters {name}", lambda: _save(gsi.stage_shelters(pref, _pref_area(pref)), "shelters", pref))
        if "flood" in stages and _path("municipalities", pref).exists():
            meshes |= first_meshes(_pref_area(pref))

    if "flood" in stages:
        available = catalog.flood_meshes()
        todo = [(m, c, u) for m in sorted(meshes & set(available)) for c, u in sorted(available[m].items())]
        log.info("flood: %d mesh files cover these prefectures", len(todo))
        for i, (mesh, cls, url) in enumerate(todo, 1):
            src = f"{mesh}_{cls}"
            if r.done("flood_depth", src):
                continue

            def run(mesh=mesh, cls=cls, url=url, src=src):
                for layer, gdf in ksj.stage_flood_mesh(mesh, cls, url).items():
                    _save(gdf, layer, src)
            r.run(f"flood [{i}/{len(todo)}] {src}", run)

    if r.failed:
        log.error("%d items failed:\n  %s", len(r.failed), "\n  ".join(r.failed))
        raise SystemExit(1)


def _own_codes(city_code: str) -> set[str]:
    """The municipality codes a PLATEAU dataset owns: itself, or all wards of a designated city."""
    pref = city_code[:2]
    _pref_area(pref)  # makes sure municipalities for the prefecture are staged
    munis = gpd.read_parquet(_path("municipalities", pref))
    city_name = plateau.catalog_entry(city_code)["city"]
    wards = set(munis.loc[(munis["city_ja"] == city_name) & munis["ward_ja"].notna(), "code"])
    if not wards:
        return {city_code}
    # Ward codes that no longer exist (e.g. Hamamatsu merged 7 wards into 3 in 2024) are still used by
    # older PLATEAU data. A designated city's wards are numbered just above it (22130 -> 22131..),
    # so claim the codes in that range that are neither another municipality nor another PLATEAU city.
    others = set(munis["code"]) - wards
    catalog = set(plateau.all_city_codes())
    old = {f"{n:05d}" for n in range(int(city_code) + 1, int(city_code) + 30)} - others - catalog
    return {city_code, *wards, *old}


def cmd_plateau(args) -> None:
    if args.cities == ["all"]:
        major = config.TOKYO_23 + list(config.MAJOR_CITIES)
        codes = major + [c for c in plateau.all_city_codes() if c not in major]
    elif args.cities == ["major"]:
        codes = config.TOKYO_23 + list(config.MAJOR_CITIES)
    elif args.cities == ["tokyo23"]:
        codes = config.TOKYO_23
    else:
        codes = args.cities
    r = Runner(args.force)
    for code in codes:
        legacy = config.STAGED_DIR / "plateau" / f"{code}.parquet"  # Tokyo wards staged before v2
        if r.done("buildings", code) or (not args.force and legacy.exists()):
            log.info("cached %s", code)
            continue

        def run(code=code):
            _save(plateau.stage_city(code, _own_codes(code)), "buildings", code)
        r.run(f"PLATEAU {code} {config.MAJOR_CITIES.get(code, '')}".rstrip(), run)
    if r.failed:
        log.error("%d cities failed:\n  %s", len(r.failed), "\n  ".join(r.failed))
        raise SystemExit(1)


def cmd_load(args) -> None:
    load.load_all(args.layers, reset=args.reset, force=args.force, database_url=args.database_url)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="koko_pipeline", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage", help="download + stage KSJ hazard layers and GSI shelters per prefecture")
    s.add_argument("--prefs", nargs="+", required=True, help="prefecture codes (01-47), or 'all'")
    s.add_argument("--only", nargs="+", choices=STAGES)
    s.add_argument("--force", action="store_true", help="re-stage even if already staged")
    s.set_defaults(func=cmd_stage)

    s = sub.add_parser("plateau", help="stage PLATEAU buildings per city")
    s.add_argument("--cities", nargs="+", default=["tokyo23"], help="city codes, 'tokyo23', 'major' or 'all' (every PLATEAU city)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_plateau)

    s = sub.add_parser("load", help="load staged files into PostGIS (only new or changed files)")
    s.add_argument("--layers", nargs="+", choices=list(load.LAYERS))
    s.add_argument("--reset", action="store_true", help="drop and recreate the koko schema first")
    s.add_argument("--force", action="store_true", help="reload every staged file")
    s.add_argument("--database-url", default=None)
    s.set_defaults(func=cmd_load)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
