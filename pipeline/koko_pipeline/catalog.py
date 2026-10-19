"""Discover download URLs from the KSJ dataset pages (newest file per prefecture / mesh)."""
from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urljoin

import requests

from . import config
from .download import USER_AGENT


@lru_cache(maxsize=None)
def _page(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _links(dataset: str) -> list[tuple[re.Match, str]]:
    d = config.DATASETS[dataset]
    page = _page(d["page"])
    pattern = re.compile(d["link"])
    out, seen = [], set()
    for m in re.finditer(r"[\w./-]*data/[A-Za-z0-9]+/[\w./-]+?\.zip", page):
        href = m.group(0)
        hit = pattern.search(href)
        if hit and hit.group(0) not in seen:
            seen.add(hit.group(0))
            out.append((hit, urljoin(f"{config.KSJ}/", hit.group(0))))
    return out


def latest_by_pref(dataset: str) -> dict[str, str]:
    """{pref_code: url} for datasets published per prefecture in several years (A40, A49)."""
    best: dict[str, tuple[int, str]] = {}
    for hit, url in _links(dataset):
        pref, year = hit.group("pref"), int(hit.group("year"))
        if pref in config.PREFECTURES and (pref not in best or year > best[pref][0]):
            best[pref] = (year, url)
    return {p: u for p, (_, u) in sorted(best.items())}


def flood_meshes() -> dict[str, dict[str, str]]:
    """{mesh: {'10': url, '20': url}}; class 10 = designated rivers, 20 = other rivers."""
    out: dict[str, dict[str, str]] = {}
    for hit, url in _links("flood"):
        out.setdefault(hit.group("mesh"), {})[hit.group("cls")] = url
    return dict(sorted(out.items()))


def per_pref_url(dataset: str, pref: str) -> str:
    return config.DATASETS[dataset]["url"].format(pref=pref)
