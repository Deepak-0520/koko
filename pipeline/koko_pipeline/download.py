"""HTTP download helpers with caching, plus zip member lookup for Japanese file names."""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import requests

log = logging.getLogger(__name__)
USER_AGENT = "koko-pipeline/0.1 (+https://github.com/; open-data research)"


def fetch(url: str, dest_dir: Path) -> Path:
    """Download url into dest_dir once; resume if a partial file exists."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / url.rsplit("/", 1)[-1]
    part = dest.with_suffix(dest.suffix + ".part")
    if dest.exists():
        log.info("cached %s", dest.name)
        return dest

    headers = {"User-Agent": USER_AGENT}
    offset = part.stat().st_size if part.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        mode = "ab" if offset and r.status_code == 206 else "wb"
        log.info("downloading %s", url)
        with open(part, mode) as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    part.rename(dest)
    return dest


def zip_member_name(info: zipfile.ZipInfo) -> str:
    """Decode a zip member name; KSJ zips store Shift_JIS names without the UTF-8 flag."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp932")
    except UnicodeDecodeError:
        return info.filename


def find_members(zip_path: Path, *, suffix: str, contains: str = "") -> list[tuple[str, str]]:
    """Return [(raw_name, decoded_name)] for members ending with suffix and containing a substring."""
    with zipfile.ZipFile(zip_path) as z:
        out = []
        for info in z.infolist():
            name = zip_member_name(info)
            if name.endswith(suffix) and contains in name:
                out.append((info.filename, name))
        return out


def vsizip(zip_path: Path, member: str) -> str:
    return f"/vsizip/{zip_path.as_posix()}/{member}"
