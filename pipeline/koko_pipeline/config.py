"""Static configuration: target area, source URLs, code lists."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("KOKO_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
STAGED_DIR = DATA_DIR / "staged"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://koko:koko@127.0.0.1:5432/koko")

# Tokyo 23 special wards: code -> (Japanese, English)
TOKYO_23 = {
    "13101": ("千代田区", "Chiyoda"),
    "13102": ("中央区", "Chuo"),
    "13103": ("港区", "Minato"),
    "13104": ("新宿区", "Shinjuku"),
    "13105": ("文京区", "Bunkyo"),
    "13106": ("台東区", "Taito"),
    "13107": ("墨田区", "Sumida"),
    "13108": ("江東区", "Koto"),
    "13109": ("品川区", "Shinagawa"),
    "13110": ("目黒区", "Meguro"),
    "13111": ("大田区", "Ota"),
    "13112": ("世田谷区", "Setagaya"),
    "13113": ("渋谷区", "Shibuya"),
    "13114": ("中野区", "Nakano"),
    "13115": ("杉並区", "Suginami"),
    "13116": ("豊島区", "Toshima"),
    "13117": ("北区", "Kita"),
    "13118": ("荒川区", "Arakawa"),
    "13119": ("板橋区", "Itabashi"),
    "13120": ("練馬区", "Nerima"),
    "13121": ("足立区", "Adachi"),
    "13122": ("葛飾区", "Katsushika"),
    "13123": ("江戸川区", "Edogawa"),
}

# Generous bbox around the 23 wards (lon_min, lat_min, lon_max, lat_max), used for fast pre-filtering
TOKYO_23_BBOX = (139.55, 35.48, 139.93, 35.83)

KSJ = "https://nlftp.mlit.go.jp/ksj/gml/data"
SOURCES = {
    "wards": {
        "url": f"{KSJ}/N03/N03-2025/N03-20250101_13_GML.zip",
        "title": "国土数値情報 行政区域 (N03) 2025",
    },
    "flood_designated": {
        "url": f"{KSJ}/A31b/A31b-25/A31b-25_10_5339_GEOJSON.zip",
        "title": "国土数値情報 洪水浸水想定区域 (A31b) 2025 — 洪水予報河川・水位周知河川",
    },
    "flood_other": {
        "url": f"{KSJ}/A31b/A31b-25/A31b-25_20_5339_GEOJSON.zip",
        "title": "国土数値情報 洪水浸水想定区域 (A31b) 2025 — その他の河川",
    },
    "landslide": {
        "url": f"{KSJ}/A33/A33-23/A33-23_13_GML.zip",
        "title": "国土数値情報 土砂災害警戒区域 (A33) 2023",
    },
    "tsunami": {
        "url": f"{KSJ}/A40/A40-23/A40-23_13_GML.zip",
        "title": "国土数値情報 津波浸水想定 (A40) 2023",
    },
    "storm_surge": {
        "url": f"{KSJ}/A49/A49-20/A49-20_13_GML.zip",
        "title": "国土数値情報 高潮浸水想定区域 (A49) 2020",
    },
    "shelters": {
        "url": "https://cyberjapandata.gsi.go.jp/xyz/skhb0{n}/{z}/{x}/{y}.geojson",
        "title": "国土地理院 指定緊急避難場所データ (skhb01–08)",
    },
    "plateau": {
        "url": "https://api.plateau.reearth.io/datacatalog/plateau-datasets",
        "title": "Project PLATEAU 3D都市モデル 建築物 (CityGML)",
    },
}

# KSJ code lists (https://nlftp.mlit.go.jp/ksj/gml/codelist/)
FLOOD_DEPTH_RANK = {  # water_depth_code: rank -> (min_m, max_m)
    1: (0.0, 0.5),
    2: (0.5, 3.0),
    3: (3.0, 5.0),
    4: (5.0, 10.0),
    5: (10.0, 20.0),
    6: (20.0, None),
}
FLOOD_DURATION_RANK = {  # flood_duration_code: rank -> (min_h, max_h)
    1: (0, 12),
    2: (12, 24),
    3: (24, 72),
    4: (72, 168),
    5: (168, 336),
    6: (336, 672),
    7: (672, None),
}
LANDSLIDE_PHENOMENON = {1: "steep_slope_failure", 2: "debris_flow", 3: "landslide"}
LANDSLIDE_ZONE = {1: "warning", 2: "special_warning", 3: "warning_pending", 4: "special_warning_pending"}

# GSI shelter tile sets: skhb0N -> disaster type
SHELTER_TYPES = {
    1: "flood",
    2: "landslide",
    3: "storm_surge",
    4: "earthquake",
    5: "tsunami",
    6: "fire",
    7: "inland_flood",
    8: "volcano",
}
