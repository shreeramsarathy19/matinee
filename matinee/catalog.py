"""Group episodes into series, work out "next up", and build the Netflix-style rows.

Works on the serialized video dicts produced by app.serialize() (title, folder,
subfolder, added_at, played_at, position, finished, ...).
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

EP_RE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,2})E(\d{1,3})", re.I)
SEASON_DIR = re.compile(r"^(?:Season|Series|S)\s*0*(\d{1,3})$", re.I)
NEW_DAYS = 14


def episode_key(v: dict) -> Optional[Tuple[int, int]]:
    m = EP_RE.search(v["title"])
    if m:
        return int(m.group(1)), int(m.group(2))
    sm = SEASON_DIR.match(v.get("subfolder") or "")
    if sm:
        em = re.search(r"(?<!\d)(\d{1,3})(?!\d)", v["title"])
        return int(sm.group(1)), int(em.group(1)) if em else 0
    # "01 - Rebirth", "02. Confrontation": leading number = episode (not for the Movies folder)
    if (v.get("folder") or "").lower() != "movies":
        lm = re.match(r"^(\d{1,3})\b", v["title"])
        if lm:
            return 1, int(lm.group(1))
    return None


def is_series(videos: List[dict]) -> bool:
    """A folder is a series when its files look like episodes (SxxEyy titles or Season subfolders)."""
    if not videos:
        return False
    hits = sum(1 for v in videos if episode_key(v) is not None)
    return hits >= max(1, len(videos) // 2)


def episode_label(v: dict) -> str:
    """'Designated Survivor - S01E11 - Warriors' in folder 'Designated Survivor' -> 'S01E11 - Warriors'."""
    t = v["title"]
    prefix = v["folder"]
    if t.lower().startswith(prefix.lower()):
        t = t[len(prefix):]
    return t.strip(" -–—") or v["title"]


def _order(videos: List[dict]) -> List[dict]:
    return sorted(videos, key=lambda v: (episode_key(v) or (999, 999), v["title"].lower()))


def next_up(videos: List[dict]) -> Optional[dict]:
    """What to play when the user hits Play on a series: the episode touched LAST.
    Unfinished -> resume it. Finished -> the next unwatched episode after it —
    never an older half-watched episode (jumping from a finished E07 back to a
    half-seen E02 is exactly what users don't want)."""
    eps = _order(videos)
    if not eps:
        return None
    played = [v for v in eps if v.get("played_at")]
    if not played:
        return eps[0]
    last = max(played, key=lambda v: v["played_at"])
    if not last["finished"]:
        return last
    idx = eps.index(last)
    for v in eps[idx + 1:]:
        if not v["finished"]:
            return v
    return eps[idx + 1] if idx + 1 < len(eps) else last


def next_after(videos: List[dict], video_id: str) -> Optional[dict]:
    """The episode that follows `video_id` in its folder (None for movies / last one)."""
    if not is_series(videos):
        return None
    eps = _order(videos)
    ids = [e["id"] for e in eps]
    if video_id not in ids:
        return None
    idx = ids.index(video_id)
    return eps[idx + 1] if idx + 1 < len(eps) else None


def build(videos: List[dict]) -> Dict[str, dict]:
    """folder -> {"series": bool, "videos": [...]}"""
    folders: Dict[str, List[dict]] = {}
    for v in videos:
        folders.setdefault(v["folder"], []).append(v)
    return {name: {"series": is_series(vids), "videos": vids} for name, vids in folders.items()}


def continue_row(cat: Dict[str, dict], limit: int = 20) -> List[dict]:
    """The Continue Watching rail:
    - one card per series — what next_up says (the last-touched episode, or the
      one after it when that was finished); nothing when the series is done
    - movies: each one started but not finished
    Ordered by when the series/movie was last touched."""
    out: List[dict] = []
    for folder, info in cat.items():
        vids = info["videos"]
        if info["series"]:
            played = [v for v in vids if v.get("played_at")]
            if not played:
                continue
            nxt = next_up(vids)
            if not nxt or nxt.get("finished"):
                continue
            item = dict(nxt)
            item["_touched"] = max(v["played_at"] for v in played)
            out.append(item)
        else:
            for v in vids:
                if v.get("played_at") and not v.get("finished") and (v.get("position") or 0) > 5:
                    item = dict(v)
                    item["_touched"] = v["played_at"]
                    out.append(item)
    out.sort(key=lambda x: -x["_touched"])
    for item in out:
        item.pop("_touched", None)
    return out[:limit]


def series_entry(folder: str, videos: List[dict], now: Optional[float] = None) -> dict:
    now = now or time.time()
    cutoff = now - NEW_DAYS * 86400
    added = [v["added_at"] or 0 for v in videos]
    first, latest = min(added), max(added)
    new_count = sum(1 for a in added if a >= cutoff)
    if first >= cutoff:
        flag = "New series"
    elif new_count:
        flag = "New episodes"
    else:
        flag = None
    nxt = next_up(videos)
    watched = sum(1 for v in videos if v["finished"])
    seasons = sorted({(episode_key(v) or (0, 0))[0] for v in videos if episode_key(v)})
    return {
        "type": "series",
        "folder": folder,
        "title": folder,
        "count": len(videos),
        "seasons": len(seasons),
        "watched": watched,
        "new_count": new_count,
        "flag": flag,
        "latest_added": latest,
        "next": nxt,
        "percent": nxt["percent"] if nxt else 0,
        "ios_ok": all(v["ios_ok"] for v in videos),
    }


def movie_entry(v: dict, now: Optional[float] = None) -> dict:
    now = now or time.time()
    return {"type": "movie", "video": v, "flag": "New" if (v["added_at"] or 0) >= now - NEW_DAYS * 86400 else None,
            "latest_added": v["added_at"] or 0}


def recent_entries(videos: List[dict], limit: int = 20) -> List[dict]:
    """One card per series or movie, newest first — not one per episode."""
    cat = build(videos)
    entries: List[dict] = []
    for folder, info in cat.items():
        if info["series"]:
            entries.append(series_entry(folder, info["videos"]))
        else:
            entries.extend(movie_entry(v) for v in info["videos"])
    entries.sort(key=lambda e: e["latest_added"], reverse=True)
    return entries[:limit]


def show_detail(folder: str, videos: List[dict]) -> dict:
    eps = _order(videos)
    seasons: Dict[int, List[dict]] = {}
    for v in eps:
        key = episode_key(v)
        s = key[0] if key else 0
        item = dict(v)
        item["season"] = s
        item["episode"] = key[1] if key else None
        item["label"] = episode_label(v)
        seasons.setdefault(s, []).append(item)
    entry = series_entry(folder, videos)
    entry["seasons_list"] = [
        {"number": s, "name": f"Season {s}" if s else "Episodes", "episodes": items}
        for s, items in sorted(seasons.items())
    ]
    return entry


def search(videos: List[dict], q: str, limit: int = 60) -> List[dict]:
    """Series whose name matches (one entry) and movies that match — episodes are
    reached from the series page or the in-player list, never from search."""
    ql = q.strip().lower()
    if not ql:
        return []
    cat = build(videos)
    out: List[dict] = []
    for folder, info in cat.items():
        if info["series"]:
            if ql in folder.lower():
                out.append(series_entry(folder, info["videos"]))
        else:
            for v in info["videos"]:
                if ql in v["title"].lower() or ql in folder.lower():
                    out.append({"type": "movie", "video": v, "flag": None})
    return out[:limit]
