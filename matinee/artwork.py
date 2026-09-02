"""Poster artwork for shows/movies, fetched from free public APIs and approved by
the user per show. TVmaze (TV) and the iTunes Search API (movies) — no API keys.
Approved images are stored in data/posters/ and served at /poster/<folder>."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from .config import DATA_DIR

log = logging.getLogger("matinee.artwork")

POSTER_DIR = DATA_DIR / "posters"
HEADERS = {"User-Agent": "Matinee/1.0 (personal media server)"}
# Downloads are only allowed from the hosts the search APIs themselves return.
ALLOWED_HOSTS = re.compile(r"(^|\.)(tvmaze\.com|mzstatic\.com|wikimedia\.org)$")


def poster_path(folder: str) -> Path:
    return POSTER_DIR / (hashlib.sha1(folder.encode("utf-8")).hexdigest()[:16] + ".jpg")


def poster_url(folder: str) -> Optional[str]:
    return f"/poster/{urllib.parse.quote(folder)}" if poster_path(folder).is_file() else None


def _get(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _clean_query(folder: str) -> str:
    q = re.sub(r"\(\d{4}\)", " ", folder)     # drop "(2005)"
    return re.sub(r"\s+", " ", q).strip()


def search(folder: str, kind: str = "series") -> List[Dict]:
    """Poster candidates for a name, best matches first (movies ask iTunes first)."""
    q = urllib.parse.quote(_clean_query(folder))
    tv, movies = [], []
    try:
        for hit in json.loads(_get(f"https://api.tvmaze.com/search/shows?q={q}"))[:5]:
            show = hit.get("show") or {}
            img = show.get("image") or {}
            if img.get("original"):
                tv.append({
                    "title": show.get("name") or folder,
                    "year": (show.get("premiered") or "")[:4],
                    "thumb": img.get("medium") or img["original"],
                    "url": img["original"],
                    "source": "TVmaze",
                    "tvmaze_id": show.get("id"),
                })
    except Exception as e:
        log.info("tvmaze search failed for %s: %s", folder, e)
    if kind == "movie":
        movies = _wiki_movies(_clean_query(folder))
    out = movies + tv if kind == "movie" else tv + movies
    return out[:8]


def _wiki_movies(name: str) -> List[Dict]:
    """Film posters via Wikipedia (their film pages carry the official poster).
    Politely throttled — Wikipedia 429s on bursts."""
    import time as _t

    out: List[Dict] = []
    try:
        hits = json.loads(_get(
            f"https://en.wikipedia.org/w/rest.php/v1/search/title?q={urllib.parse.quote(name + ' film')}&limit=4"
        )).get("pages", [])
        hits += json.loads(_get(
            f"https://en.wikipedia.org/w/rest.php/v1/search/title?q={urllib.parse.quote(name)}&limit=2"
        )).get("pages", [])
    except Exception as e:
        log.info("wikipedia search failed for %s: %s", name, e)
        return out
    seen = set()
    for h in hits:
        key = h.get("key")
        if not key or key in seen or len(out) >= 4:
            continue
        seen.add(key)
        try:
            _t.sleep(0.35)
            summ = json.loads(_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(key)}"))
        except Exception:
            continue
        desc = (summ.get("description") or "").lower()
        img = (summ.get("originalimage") or {}).get("source")
        thumb = (summ.get("thumbnail") or {}).get("source") or img
        if img and ("film" in desc or "movie" in desc):
            year = ""
            m = re.search(r"(19|20)\d{2}", desc)
            if m:
                year = m.group(0)
            out.append({"title": summ.get("title") or name, "year": year,
                        "thumb": thumb, "url": img, "source": "Wikipedia"})
    return out


def set_poster(folder: str, url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    if not (url.startswith("https://") and ALLOWED_HOSTS.search(host)):
        raise ValueError("Image URL not from a supported source")
    data = _get(url, timeout=25)
    if len(data) < 2000:
        raise ValueError("Image download failed")
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    poster_path(folder).write_bytes(data)
    return poster_url(folder) or ""


def remove(folder: str) -> None:
    try:
        poster_path(folder).unlink()
    except FileNotFoundError:
        pass
