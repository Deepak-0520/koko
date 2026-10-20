"""Unit tests for the pure logic (no database needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

import pytest

from app.report import vertical_evacuation
from koko_pipeline.ksj import parse_depth_label


@pytest.mark.parametrize("label, expected", [
    ("0.3m以上～1.0m未満", (0.3, 1.0)),
    ("1m以上 ～ 3m未満", (1.0, 3.0)),
    ("～0.3m未満", (0.0, 0.3)),
    ("0.3m未満", (0.0, 0.3)),
    ("20m以上", (20.0, None)),
    (None, (None, None)),
])
def test_parse_depth_label(label, expected):
    assert parse_depth_label(label) == expected


def flood(rank=2, lo=0.5, hi=3.0, duration_rank=None, flow=False, erosion=False):
    return {
        "in_zone": True,
        "max_assumed": {"rank": rank, "min": lo, "max": hi, "unit": "m"},
        "duration": {"rank": duration_rank} if duration_rank else None,
        "house_collapse_zone": {"flood_flow": flow, "bank_erosion": erosion},
    }


def building(depth=None, storeys=5, height=15.0):
    return {"flood_max_depth_m": depth, "storeys_above": storeys, "height_m": height}


def test_not_in_flood_zone():
    assert vertical_evacuation(building(), {"in_zone": False}) is None


def test_uses_plateau_depth_when_consistent_with_zone():
    r = vertical_evacuation(building(depth=2.0), flood())
    assert r["depth_basis"] == "plateau_building"
    assert r["lowest_safe_floor"] == 2  # (2.0 + 0.5) / 3.0 -> ceil 1, +1
    assert r["verdict"] == "upper_floors_ok"


def test_falls_back_to_zone_upper_bound_when_plateau_is_lower_than_zone():
    r = vertical_evacuation(building(depth=0.1), flood())
    assert r["depth_basis"] == "zone_rank_upper_bound"
    assert r["expected_depth_m"] == 3.0
    assert r["lowest_safe_floor"] == 3


def test_low_building_must_be_left():
    r = vertical_evacuation(building(depth=4.0, storeys=2, height=6.0), flood(rank=3, lo=3.0, hi=5.0))
    assert r["verdict"] == "leave_building"


def test_house_collapse_zone_overrides_floors():
    r = vertical_evacuation(building(depth=1.0, storeys=10, height=30.0), flood(flow=True))
    assert r["verdict"] == "leave_building"


def test_long_duration_is_flagged():
    r = vertical_evacuation(building(depth=1.0), flood(duration_rank=4))
    assert r["isolation_24h_or_more"] is True
    assert "24 h" in r["reason"]


def test_no_building_data():
    r = vertical_evacuation(None, flood())
    assert r["verdict"] == "unknown"


def test_first_mesh_codes():
    from app.report import first_mesh
    assert first_mesh(35.6812, 139.7671) == "5339"  # Tokyo Station
    assert first_mesh(34.7025, 135.4959) == "5235"  # Osaka Umeda
    assert first_mesh(43.0687, 141.3508) == "6441"  # Sapporo


def test_first_meshes_cover_an_area():
    from shapely.geometry import box

    from koko_pipeline.cli import first_meshes
    assert first_meshes(box(139.7, 35.6, 139.8, 35.7)) == {"5339"}
    assert first_meshes(box(135.3, 34.6, 135.6, 34.7)) == {"5135", "5235"}  # crosses 34°40'


def test_area_stats_tool_rejects_bad_codes():
    import asyncio
    import json

    from app.assistant import _run_tool
    for bad in [{}, {"code": "123"}, {"code": "abcde"}, {"code": 13123}]:
        text, is_error = asyncio.run(_run_tool(None, "get_area_stats", bad))  # never reaches the database
        assert is_error and "INVALID_INPUT" in json.loads(text)
