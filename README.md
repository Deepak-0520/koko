# KOKO (ここ)

**Disaster risk for any address in Japan, explained by AI in your language, grounded in official open data.**

KOKO combines Japan's national hazard datasets (MLIT 国土数値情報, PLATEAU 3D city models, GSI evacuation sites, NIED J-SHIS) into one click-for-a-report map and an AI assistant. The assistant answers questions like *"I want to rent here with my 2-year-old. Is it safe?"* in any language. Every number in its answers comes from a database lookup, never from the model's memory.

See [PLAN.md](PLAN.md) for the product plan.

## Features

- **Globe explorer** (MapLibre 5): a 3D globe with the 47 prefectures; hover highlights and names an area, click (or the dropdowns) drills down **Japan → prefecture (都道府県) → municipality (市区町村)** with a breadcrumb to step back
- **Loads only the selected place:** choosing a municipality dims everything else and loads just its layers as vector tiles from PostGIS: PLATEAU 3D buildings, flood depth, storm surge, tsunami, landslide zones and evacuation sites
- **Area statistics** for every prefecture and municipality: share of the area in each hazard zone, buildings by expected flood depth (and how many low-rise buildings face 3 m+), shelters by hazard type, 30-year shaking odds
- **3D buildings with the water line:** PLATEAU buildings at their real height; the part below the expected flood depth is drawn blue
- **Click-for-report** for any point: risk chips, worst-case flood depth and duration, house-collapse zones, **which floor of the building stays dry**, landslide / storm surge / tsunami zones, 30-year shaking probabilities, and the nearest evacuation site *rated for each hazard present*
- **Fully bilingual (日本語 / English)**: every label, report, map layer, legend and error switches instantly with the header toggle, using official Japanese terms (洪水浸水想定区域, 土砂災害警戒区域, 震度6弱…); the choice is remembered
- **Ask KOKO (AI)**: a Claude agent (`claude-opus-5-5`) with two tools, `search_address` (GSI) and `get_risk_report` (our PostGIS). It answers in the user's language, compares places, marks what it checked on the map, and must say when data is missing (*"no published data"* is not *"safe"*)

## How it works

```
国土数値情報 (A31b flood, A33 landslide, A40 tsunami, A49 storm surge, N03 boundaries)
GSI 指定緊急避難場所 tiles · PLATEAU CityGML (HTTP range requests)       J-SHIS API (live)
                │  pipeline: discover newest file per prefecture → stage GeoParquet → COPY
                ▼                                                            │
        PostGIS (Docker) ─── FastAPI /report, /areas/*, /tiles/*.pbf, /chat ──┘
                                            │                 │
                          MapLibre globe explorer       Claude tool-use loop (SSE streaming)
```

Design choices worth knowing:
- **Safety first when sources disagree:** PLATEAU's per-building flood depth is used only when it agrees with the zone at the exact point; otherwise the zone's upper bound. House-collapse zones override "go upstairs".
- **"Not in a zone" vs "no data":** the report tracks which datasets exist for each prefecture and flood mesh, so the UI and the AI never present missing data as safety.
- **Load only what's selected:** vector tiles are cut per municipality in SQL (tile ∩ municipality bbox, then the subdivided boundary), so a tile query touches thousands of polygons, not millions.
- **Statistics by sampling:** hazard-zone shares come from seeded random points inside the area (deterministic, ~2 s), cached in `area_stats` until newer data is loaded.
- **Incremental national loads:** every row carries its source file (`src`), and `loaded_files` records what's in the database, so an interrupted national run resumes where it stopped.

## Run it

Needs Docker Desktop and Python 3.11+.

```bash
docker compose up -d --wait
```

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e pipeline -r api/requirements.txt pytest
```

Stage data (start small with one prefecture, e.g. `--prefs 27` for Osaka; `all` takes a few hours):

```bash
.venv/Scripts/python -m koko_pipeline stage --prefs all
```

```bash
.venv/Scripts/python -m koko_pipeline plateau --cities major
```

```bash
.venv/Scripts/python -m koko_pipeline load
```

The loader also builds the explorer's display boundaries (`sql/003_areas.sql`) whenever municipality boundaries change. To rebuild them by hand:

```bash
docker exec -i koko-db psql -U koko -d koko < pipeline/sql/003_areas.sql
```

For the AI chat, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Then start the server:

```bash
cd api && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000 (dashboard) or http://127.0.0.1:8000/docs (API).

## Layout

```
pipeline/koko_pipeline/   catalog → download → stage (data/staged/<layer>/<src>.parquet) → load (COPY)
  catalog.py              finds the newest KSJ file per prefecture / flood mesh from the dataset pages
  ksj.py                  国土数値情報 layers (GeoJSON or Shapefile, per prefecture or mesh)
  gsi.py                  GSI shelter tiles, cached, assigned to one prefecture by address
  plateau.py              PLATEAU CityGML via HTTP range requests: only udx/bldg + codelists
  load.py                 incremental COPY loader; sql/ has the schema
api/app/                  FastAPI: /report, /coverage, /chat; areas.py = /areas/* boundaries + stats, /tiles/{layer}/{z}/{x}/{y}.pbf
  assistant.py            Ask KOKO: Claude tool-use loop, streamed as Server-Sent Events
web/                      globe explorer (app.js), point report (report_view.js), chat (chat.js), i18n.js = all EN/JA strings
tests/                    logic tests (pytest)
```

## Data notes

- **Coverage varies by dataset:** landslide zones cover all 47 prefectures (2025); tsunami covers 39 coastal prefectures and storm surge 15, each in the newest edition published (2016–2024). PLATEAU buildings cover every city PLATEAU publishes (306 cities in 45 prefectures with `--cities all`); elsewhere the report falls back to zone-level flood depth.
- **PLATEAU sentinels:** 9999 / -9999 (unknown storeys or height) are stored as NULL.
- **Licences:** 国土数値情報, PLATEAU, GSI, and J-SHIS each have attribution terms. Check them before any commercial use.
- Shelter distances are straight-line; walking routes are a later phase.
