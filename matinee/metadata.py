"""Show metadata from TVmaze (free, no key): genres, summaries, episode names,
synopses and still images. Cached in data/metadata/ so it works offline after
the first fetch. The TVmaze show id is pinned when the user approves a TVmaze
poster; otherwise the best search match is used."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from .config import DATA_DIR

log = logging.getLogger("matinee.metadata")

META_DIR = DATA_DIR / "metadata"
MAP_PATH = META_DIR / "map.json"
HEADERS = {"User-Agent": "Matinee/1.0 (personal media server)"}
REFRESH_SECONDS = 7 * 86400   # re-fetch weekly (new episodes of running shows)


def _cache_path(folder: str) -> Path:
    return META_DIR / (hashlib.sha1(folder.encode("utf-8")).hexdigest()[:16] + ".json")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read()


def _strip_html(s: Optional[str]) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _clean_query(folder: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\(\d{4}\)", " ", folder)).strip()


def has_movie_cache(title: str) -> bool:
    return (META_DIR / ("movie-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16] + ".json")).exists()


def movie_info(title: str, fetch: bool = True) -> Dict:
    """Wikipedia summary for a film: year, one-liner, and the intro paragraph.
    Cached like show metadata; misses are cached too (retried weekly)."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    path = META_DIR / ("movie-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16] + ".json")
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
        age = time.time() - data.get("fetched_at", 0)
        if data.get("miss") and age < REFRESH_SECONDS:
            return {}
        if not data.get("miss") and age < 30 * 86400:
            return {k: v for k, v in data.items() if k != "fetched_at"}
    if not fetch:
        return {}   # cache miss and fetching not allowed (fast page load / offline)
    q = _clean_query(title)
    ym = re.search(r"\((\d{4})\)", title)
    want_year = ym.group(1) if ym else ""
    info: Dict = {}
    fallback: Dict = {}
    try:
        # with a year in the name, the year-qualified search is far more precise
        # ("Gladiator 2000 film" finds Gladiator_(2000_film); "Gladiator film" doesn't)
        queries = ([f"{q} {want_year} film"] if want_year else []) + [f"{q} film"]
        hits, seen_keys = [], set()
        for query in queries:
            for h in json.loads(_get(
                    "https://en.wikipedia.org/w/rest.php/v1/search/title?q="
                    + urllib.parse.quote(query) + "&limit=5")).get("pages", []):
                if h.get("key") and h["key"] not in seen_keys:
                    seen_keys.add(h["key"])
                    hits.append(h)
        for h in hits:
            key = h.get("key")
            if not key:
                continue
            try:
                summ = json.loads(_get(
                    "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(key)))
            except Exception:
                continue
            desc = summ.get("description") or ""
            dl = desc.lower()
            if "film" not in dl and "movie" not in dl:
                continue
            # pages ABOUT the film's music/book/etc. also mention "film" — skip them
            if re.search(r"soundtrack|album|song|score|novel|book|video game|character|disambig", dl):
                continue
            year_m = (re.search(r"(19|20)\d{2}", desc)
                      or re.search(r"(19|20)\d{2}", (summ.get("extract") or "")[:120]))
            year = year_m.group(0) if year_m else ""
            cand = {"title": summ.get("title") or q, "year": year, "description": desc,
                    "extract": (summ.get("extract") or "").strip()}
            if not fallback:
                fallback = cand
            if not want_year or year == want_year:
                info = cand
                break
        if not info:
            info = fallback
    except Exception as e:
        log.info("movie info lookup failed for %s: %s", title, e)
    payload = dict(info) if info else {"miss": True}
    payload["fetched_at"] = time.time()
    path.write_text(json.dumps(payload))
    return info


# ------------------------------------------------------------- id mapping

def _load_map() -> Dict[str, int]:
    try:
        return json.loads(MAP_PATH.read_text())
    except Exception:
        return {}


def pin_show(folder: str, tvmaze_id: int) -> None:
    """Remember which TVmaze show this folder is (set when a TVmaze poster is approved)."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    m = _load_map()
    if m.get(folder) != tvmaze_id:
        m[folder] = tvmaze_id
        MAP_PATH.write_text(json.dumps(m, indent=1))
        _cache_path(folder).unlink(missing_ok=True)   # refetch under the pinned id


# ---------------------------------------------------------------- fetching

def has_cache(folder: str) -> bool:
    return _cache_path(folder).is_file()


def get(folder: str, allow_fetch: bool = False) -> Optional[Dict]:
    """Cached metadata for a show folder; optionally fetch it now if missing/stale."""
    path = _cache_path(folder)
    cached = None
    try:
        cached = json.loads(path.read_text())
    except Exception:
        pass
    fresh = cached and time.time() - cached.get("fetched_at", 0) < REFRESH_SECONDS
    if cached and (fresh or not allow_fetch):
        return None if cached.get("miss") else cached
    if not allow_fetch:
        return None if (cached or {}).get("miss") else cached
    data = _fetch(folder)
    META_DIR.mkdir(parents=True, exist_ok=True)
    if data:
        path.write_text(json.dumps(data))
        return data
    # negative cache: remember the miss so we don't hammer the API; retried weekly
    if not cached:
        path.write_text(json.dumps({"fetched_at": time.time(), "miss": True}))
        return None
    return None if cached.get("miss") else cached


def _fetch(folder: str) -> Optional[Dict]:
    try:
        show_id = _load_map().get(folder)
        if show_id:
            show = json.loads(_get(f"https://api.tvmaze.com/shows/{show_id}"))
        else:
            q = urllib.parse.quote(_clean_query(folder))
            show = json.loads(_get(f"https://api.tvmaze.com/singlesearch/shows?q={q}"))
        eps = json.loads(_get(f"https://api.tvmaze.com/shows/{show['id']}/episodes"))
    except Exception as e:
        log.info("tvmaze metadata fetch failed for %s: %s", folder, e)
        return None
    return {
        "fetched_at": time.time(),
        "tvmaze_id": show.get("id"),
        "name": show.get("name"),
        "genres": show.get("genres") or [],
        "summary": _strip_html(show.get("summary"))[:600],
        "rating": (show.get("rating") or {}).get("average"),
        "premiered": show.get("premiered"),
        "status": show.get("status"),
        "episodes": {
            f"{e.get('season')}x{e.get('number')}": {
                "name": e.get("name"),
                "summary": _strip_html(e.get("summary"))[:400],
                "image": (e.get("image") or {}).get("medium"),
                "airdate": e.get("airdate"),
                "runtime": e.get("runtime"),
            }
            for e in eps if e.get("season") is not None and e.get("number") is not None
        },
    }


# ---------------------------------------------------------------- merging

def enrich_show(detail: Dict) -> Dict:
    """Fold cached TVmaze info into a catalog.show_detail() payload.
    Cache-only: the page must open instantly — the idle worker fetches new
    shows' metadata in the background (usually within a minute of arrival)."""
    meta = get(detail["folder"], allow_fetch=False)
    if not meta:
        return detail
    detail["genres"] = meta["genres"]
    detail["summary"] = meta["summary"]
    detail["rating"] = meta["rating"]
    for season in detail.get("seasons_list", []):
        for ep in season["episodes"]:
            info = meta["episodes"].get(f"{ep.get('season')}x{ep.get('episode')}")
            if info:
                ep["ep_name"] = info["name"]
                ep["ep_summary"] = info["summary"]
                ep["ep_image"] = info["image"]
                ep["airdate"] = info["airdate"]
    return detail


def genres_index(folders: List[str]) -> Dict[str, List[str]]:
    """genre -> [folder] from cache only (no network) — for home page genre rows."""
    out: Dict[str, List[str]] = {}
    for f in folders:
        meta = get(f)
        for g in (meta or {}).get("genres") or []:
            out.setdefault(g, []).append(f)
    return out
