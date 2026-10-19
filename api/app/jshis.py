"""J-SHIS (NIED) probabilistic seismic hazard: 30-year probability of strong shaking at a point."""
from __future__ import annotations

from functools import lru_cache

import httpx

URL = "https://www.j-shis.bosai.go.jp/map/api/pshm/Y2024/AVR/TTL_MTTL/meshinfo.geojson"
TITLE = "J-SHIS 確率論的地震動予測地図 Y2024 (average case, all earthquakes)"
# JMA seismic intensity thresholds (5-lower, 5-upper, 6-lower, 6-upper)
INTENSITIES = {"T30_I45_PS": "5-", "T30_I50_PS": "5+", "T30_I55_PS": "6-", "T30_I60_PS": "6+"}


@lru_cache(maxsize=4096)
def _fetch(lat4: float, lon4: float) -> dict | None:
    r = httpx.get(URL, params={"position": f"{lon4},{lat4}", "epsg": "4326"}, timeout=8)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "Success" or not data.get("features"):
        return None
    props = data["features"][0]["properties"]
    return {
        "meshcode": props.get("meshcode"),
        "probability_30y": {label: float(props[k]) for k, label in INTENSITIES.items() if props.get(k)},
        "source": "jshis",
    }


def shaking_probability(lat: float, lon: float) -> dict | None:
    """Rounded to ~10 m so nearby lookups hit the cache (J-SHIS meshes are 250 m)."""
    try:
        return _fetch(round(lat, 4), round(lon, 4))
    except httpx.HTTPError as e:
        return {"error": f"J-SHIS unavailable: {e.__class__.__name__}", "source": "jshis"}
