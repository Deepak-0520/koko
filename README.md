# KOKO (ここ)

Address-level disaster risk for Japan, built on official open data. See [PLAN.md](PLAN.md) for the product plan.

**Status:** Phase 2. A Leaflet dashboard sits on top of the data pipeline and point-lookup API, which cover the Tokyo 23 wards. The hazard map layers themselves are nationwide.

## Dashboard

Open http://127.0.0.1:8000 after starting the API (see below).

- **Map layers:** official flood depth, flood duration, storm surge and landslide tiles (ハザードマップポータル, all of Japan); ward boundaries, evacuation sites, and PLATEAU buildings coloured by their own flood depth (from our PostGIS)
- **Click anywhere** for a report: risk level chips, worst-case flood, which floor of the building stays dry, 30-year earthquake probabilities, other hazards, and the nearest evacuation site for each danger present
- **Search** a Japanese address (GSI address search) or type `lat,lon`

Plain HTML + Leaflet 1.9.4 in `web/`, served by FastAPI. There is no build step.

## What a report contains

`GET /report?lat=35.6835&lon=139.8642` returns, for that exact point:

| Section | Source |
|---|---|
| River flood depth (max assumed L2 / planned L1), flood duration, house-collapse zones | 国土数値情報 A31b (2025) |
| The building at the point: usage, storeys, height, PLATEAU's per-building flood depth | PLATEAU CityGML (2025) |
| Vertical evacuation: lowest floor above the expected depth, or "leave the building" | derived; see `api/app/report.py` |
| Landslide warning zones | 国土数値情報 A33 (2023) |
| Storm surge / tsunami inundation depth | 国土数値情報 A49 (2020) / A40 (2023) |
| 30-year probability of shaking ≥ 5-, 5+, 6-, 6+ | J-SHIS Y2024 (live API, cached) |
| Nearest designated evacuation sites, plus the nearest site rated for each hazard present | GSI 指定緊急避難場所 |

Every section names its `source`, and `sources` lists titles and URLs.

## Run it

Needs Docker Desktop and Python 3.11+.

```bash
docker compose up -d --wait
```

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e pipeline -r api/requirements.txt pytest
```

```bash
.venv/Scripts/python -m koko_pipeline stage
```

```bash
.venv/Scripts/python -m koko_pipeline plateau --wards 13101
```

```bash
.venv/Scripts/python -m koko_pipeline load
```

```bash
cd api && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000/docs. Use `--wards all` to stage PLATEAU for all 23 wards (several GB of range requests and roughly an hour).

## Layout

```
pipeline/koko_pipeline/   download → stage (GeoParquet in data/staged) → load (COPY into PostGIS)
  ksj.py                  国土数値情報 hazard layers, clipped to the 23 wards
  gsi.py                  GSI shelter tiles (skhb01–08), merged per site
  plateau.py              PLATEAU CityGML via HTTP range requests: only udx/bldg + codelists
  load.py                 COPY loader; sql/ has the schema and post-load indexes
api/app/                  FastAPI: /health, /wards, /report, /layers/{wards,shelters,buildings}
web/                      Leaflet dashboard (index.html, app.js, style.css)
tests/                    logic tests (pytest)
```

## Data notes

- **Tsunami:** the Tokyo A40 dataset only covers the Izu and Ogasawara islands, so no 23-ward point is in a tsunami zone. The API reports `in_zone: false`, which means "not in a designated zone", not "no risk".
- **Flood depth disagreement:** PLATEAU gives each building the depth of the mesh it overlaps most, which can be lower than the zone at the exact point. Vertical-evacuation advice uses the PLATEAU value only when it agrees with the zone rank, and otherwise the zone's upper bound.
- **PLATEAU sentinels:** 9999 / -9999 (unknown storeys or height) are stored as NULL.
- **Licences:** 国土数値情報, PLATEAU, GSI, and J-SHIS each have attribution terms. Check them before any commercial use.
- Shelter distances are straight-line; walking routes are a later phase.
