"""Static configuration: prefectures, dataset pages, code lists."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("KOKO_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
STAGED_DIR = DATA_DIR / "staged"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://koko:koko@127.0.0.1:5432/koko")

PREFECTURES = {
    "01": ("北海道", "Hokkaido"), "02": ("青森県", "Aomori"), "03": ("岩手県", "Iwate"),
    "04": ("宮城県", "Miyagi"), "05": ("秋田県", "Akita"), "06": ("山形県", "Yamagata"),
    "07": ("福島県", "Fukushima"), "08": ("茨城県", "Ibaraki"), "09": ("栃木県", "Tochigi"),
    "10": ("群馬県", "Gunma"), "11": ("埼玉県", "Saitama"), "12": ("千葉県", "Chiba"),
    "13": ("東京都", "Tokyo"), "14": ("神奈川県", "Kanagawa"), "15": ("新潟県", "Niigata"),
    "16": ("富山県", "Toyama"), "17": ("石川県", "Ishikawa"), "18": ("福井県", "Fukui"),
    "19": ("山梨県", "Yamanashi"), "20": ("長野県", "Nagano"), "21": ("岐阜県", "Gifu"),
    "22": ("静岡県", "Shizuoka"), "23": ("愛知県", "Aichi"), "24": ("三重県", "Mie"),
    "25": ("滋賀県", "Shiga"), "26": ("京都府", "Kyoto"), "27": ("大阪府", "Osaka"),
    "28": ("兵庫県", "Hyogo"), "29": ("奈良県", "Nara"), "30": ("和歌山県", "Wakayama"),
    "31": ("鳥取県", "Tottori"), "32": ("島根県", "Shimane"), "33": ("岡山県", "Okayama"),
    "34": ("広島県", "Hiroshima"), "35": ("山口県", "Yamaguchi"), "36": ("徳島県", "Tokushima"),
    "37": ("香川県", "Kagawa"), "38": ("愛媛県", "Ehime"), "39": ("高知県", "Kochi"),
    "40": ("福岡県", "Fukuoka"), "41": ("佐賀県", "Saga"), "42": ("長崎県", "Nagasaki"),
    "43": ("熊本県", "Kumamoto"), "44": ("大分県", "Oita"), "45": ("宮崎県", "Miyazaki"),
    "46": ("鹿児島県", "Kagoshima"), "47": ("沖縄県", "Okinawa"),
}

# PLATEAU "major cities" set: Tokyo 23 wards plus the largest cities that have PLATEAU models
TOKYO_23 = [f"131{n:02d}" for n in range(1, 24)]
MAJOR_CITIES = {
    "01100": "Sapporo", "04100": "Sendai", "11100": "Saitama", "12100": "Chiba",
    "14100": "Yokohama", "14130": "Kawasaki", "22100": "Shizuoka", "22130": "Hamamatsu",
    "23100": "Nagoya", "26100": "Kyoto", "27100": "Osaka", "28100": "Kobe",
    "34100": "Hiroshima", "40100": "Kitakyushu", "40130": "Fukuoka", "43100": "Kumamoto",
}

KSJ = "https://nlftp.mlit.go.jp/ksj/gml"
# Dataset pages list every download; the pipeline picks the newest file per prefecture / mesh.
DATASETS = {
    "municipalities": {
        "title": "国土数値情報 行政区域 (N03) 2026",
        "page": f"{KSJ}/datalist/KsjTmplt-N03-2026.html",
        "url": f"{KSJ}/data/N03/N03-2026/N03-20260101_{{pref}}_GML.zip",
    },
    "flood": {
        "title": "国土数値情報 洪水浸水想定区域 (A31b) 2025",
        "page": f"{KSJ}/datalist/KsjTmplt-A31b-2025.html",
        "link": r"data/A31b/A31b-25/A31b-25_(?P<cls>10|20)_(?P<mesh>\d{4})_GEOJSON\.zip",
    },
    "landslide": {
        "title": "国土数値情報 土砂災害警戒区域 (A33) 2025",
        "page": f"{KSJ}/datalist/KsjTmplt-A33-2025.html",
        "url": f"{KSJ}/data/A33/A33-25/A33-25_{{pref}}_SHP.zip",
    },
    "tsunami": {
        "title": "国土数値情報 津波浸水想定 (A40)",
        "page": f"{KSJ}/datalist/KsjTmplt-A40-2024.html",
        "link": r"data/A40/A40-(?P<year>\d{2})/A40-\d{2}_(?P<pref>\d{2})_GML\.zip",
    },
    "storm_surge": {
        "title": "国土数値情報 高潮浸水想定区域 (A49)",
        "page": f"{KSJ}/datalist/KsjTmplt-A49-2024.html",
        "link": r"data/A49/A49-(?P<year>\d{2})/A49-\d{2}_(?P<pref>\d{2})_GML\.zip",
    },
    "shelters": {
        "title": "国土地理院 指定緊急避難場所データ (skhb01–08)",
        "page": "https://www.gsi.go.jp/bousaichiri/hinanbasho.html",
        "url": "https://cyberjapandata.gsi.go.jp/xyz/skhb0{n}/{z}/{x}/{y}.geojson",
    },
    "buildings": {
        "title": "Project PLATEAU 3D都市モデル 建築物 (CityGML)",
        "page": "https://www.mlit.go.jp/plateau/open-data/",
        "url": "https://api.plateau.reearth.io/datacatalog/plateau-datasets",
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
