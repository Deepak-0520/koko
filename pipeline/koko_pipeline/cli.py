"""KOKO data pipeline.

    python -m koko_pipeline stage                 # all hazard layers + shelters
    python -m koko_pipeline stage --only flood
    python -m koko_pipeline plateau --wards 13101 13102   (or --wards all)
    python -m koko_pipeline load                  # staged files -> PostGIS
"""
from __future__ import annotations

import argparse
import logging
import time

import geopandas as gpd

from . import config, gsi, ksj, load, plateau

log = logging.getLogger("koko_pipeline")
HAZARD_STAGES = ["flood", "landslide", "tsunami", "storm_surge", "shelters"]


def _save(gdf: gpd.GeoDataFrame, name: str) -> None:
    path = config.STAGED_DIR / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(path)
    log.info("staged %s: %d rows -> %s", name, len(gdf), path)


def _ward_area():
    path = config.STAGED_DIR / "wards.parquet"
    if path.exists():
        wards = gpd.read_parquet(path)
    else:
        wards = ksj.stage_wards()
        _save(wards, "wards")
    return wards.union_all()


def cmd_stage(args) -> None:
    wards = ksj.stage_wards()
    _save(wards, "wards")
    area = wards.union_all()
    for name in args.only or HAZARD_STAGES:
        t = time.perf_counter()
        if name == "flood":
            for table, gdf in ksj.stage_flood(area).items():
                _save(gdf, table)
        elif name == "shelters":
            # keep shelters ~2 km beyond the ward boundary: the nearest one may be across it
            _save(gsi.stage_shelters(area.buffer(0.02)), "shelters")
        else:
            _save(getattr(ksj, f"stage_{name}")(area), name)
        log.info("%s took %.0fs", name, time.perf_counter() - t)


def cmd_plateau(args) -> None:
    codes = list(config.TOKYO_23) if args.wards == ["all"] else args.wards
    out_dir = config.STAGED_DIR / "plateau"
    out_dir.mkdir(parents=True, exist_ok=True)
    for code in codes:
        if code not in config.TOKYO_23:
            raise SystemExit(f"unknown ward code {code}")
        path = out_dir / f"{code}.parquet"
        if path.exists() and not args.force:
            log.info("cached %s", path.name)
            continue
        t = time.perf_counter()
        gdf = plateau.stage_ward(code)
        gdf.to_parquet(path)
        log.info("staged PLATEAU %s (%s): %d buildings in %.0fs",
                 code, config.TOKYO_23[code][1], len(gdf), time.perf_counter() - t)


def cmd_load(args) -> None:
    load.load_all(database_url=args.database_url)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="koko_pipeline", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage", help="download + stage KSJ hazard layers and GSI shelters")
    s.add_argument("--only", nargs="+", choices=HAZARD_STAGES)
    s.set_defaults(func=cmd_stage)

    s = sub.add_parser("plateau", help="stage PLATEAU buildings per ward")
    s.add_argument("--wards", nargs="+", default=["13101"], help="ward codes, or 'all'")
    s.add_argument("--force", action="store_true", help="re-stage even if cached")
    s.set_defaults(func=cmd_plateau)

    s = sub.add_parser("load", help="load staged files into PostGIS")
    s.add_argument("--database-url", default=None)
    s.set_defaults(func=cmd_load)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
