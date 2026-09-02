"""Find and download subtitles from OpenSubtitles.com (the VLSub way).

Hash search first — the "moviehash" is file size + the first and last 64 KB
summed as little-endian uint64s, so a match is byte-exact and perfectly synced.
Name search (show + SxxEyy, or movie title + year) is the fallback.

The free tier allows a small number of downloads per day, so every download
goes through a local daily counter (data/opensubtitles.json) capped at
`daily_limit`; the server-reported `remaining` is also respected.
"""
from __future__ import annotations

import json
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import DATA_DIR

API = "https://api.opensubtitles.com/api/v1"
STATE_PATH = DATA_DIR / "opensubtitles.json"
USER_AGENT = "Matinee v1.0"
_lock = threading.Lock()

EP_RE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,2})E(\d{1,3})", re.I)
YEAR_RE = re.compile(r"\((\d{4})\)")


class SubsearchError(Exception):
    """User-facing failure ('quota used up', 'not configured', 'nothing found')."""


# ------------------------------------------------------------------ hashing

def oshash(path: Path) -> Optional[str]:
    """OpenSubtitles moviehash: 16 hex chars, None for files under 128 KB."""
    CHUNK = 65536
    size = path.stat().st_size
    if size < CHUNK * 2:
        return None
    h = size
    with path.open("rb") as f:
        for offset in (0, size - CHUNK):
            f.seek(offset)
            data = f.read(CHUNK)
            for (v,) in struct.iter_unpack("<Q", data):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


# ------------------------------------------------------------------ state

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st))


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def chosen_ids(video_id: str) -> set:
    """file_ids already downloaded for this video — skipped when fetching a replacement."""
    return set((_load_state().get("chosen") or {}).get(video_id, []))


def quota(limit: int) -> Dict:
    st = _load_state()
    used = st.get("used", 0) if st.get("date") == _today() else 0
    remaining = max(0, limit - used)
    if st.get("date") == _today() and st.get("server_remaining") is not None:
        remaining = min(remaining, max(0, int(st["server_remaining"])))
    return {"used": used, "limit": limit, "remaining": remaining}


# ------------------------------------------------------------------ client

def _http(method: str, url: str, headers: dict, body: Optional[dict] = None,
          timeout: int = 20) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in {"User-Agent": USER_AGENT, "Accept": "application/json", **headers}.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fetch_raw(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class OpenSubtitles:
    def __init__(self, settings: dict, http: Callable = _http, fetch: Callable = _fetch_raw):
        self.username = str(settings.get("username") or "")
        self.password = str(settings.get("password") or "")
        self.api_key = str(settings.get("api_key") or "")
        self.languages = [str(l) for l in (settings.get("languages") or ["en"])]
        self.daily_limit = int(settings.get("daily_limit") or 20)
        self.http = http
        self.fetch = fetch

    def configured(self) -> bool:
        return bool(self.username and self.password and self.api_key)

    # ---- auth -----------------------------------------------------------
    def _headers(self, token: Optional[str] = None) -> dict:
        h = {"Api-Key": self.api_key}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _token(self) -> str:
        st = _load_state()
        if st.get("token") and time.time() - st.get("token_time", 0) < 20 * 3600:
            return st["token"]
        try:
            res = self.http("POST", f"{API}/login", self._headers(),
                            {"username": self.username, "password": self.password})
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise SubsearchError("OpenSubtitles login failed — use your site USERNAME "
                                     "(not your email) and password in config.yaml")
            raise SubsearchError(f"OpenSubtitles login error ({e.code})")
        token = res.get("token")
        if not token:
            raise SubsearchError("OpenSubtitles login gave no token")
        st.update({"token": token, "token_time": time.time()})
        _save_state(st)
        return token

    # ---- search ---------------------------------------------------------
    def _search(self, params: dict) -> List[dict]:
        qs = urllib.parse.urlencode(sorted(params.items()))
        res = self.http("GET", f"{API}/subtitles?{qs}", self._headers())
        return res.get("data") or []

    @staticmethod
    def name_params(video: dict) -> dict:
        """Search params from our tidy names: 'Show - S01E03 - Title' / 'Heat (1995)'."""
        title, folder = video.get("title") or "", video.get("folder") or ""
        m = EP_RE.search(title)
        if m:
            return {"query": folder or title[: m.start()].strip(" -"),
                    "season_number": int(m.group(1)), "episode_number": int(m.group(2))}
        params = {"query": YEAR_RE.sub("", title).strip()}
        ym = YEAR_RE.search(title)
        if ym:
            params["year"] = int(ym.group(1))
        return params

    @staticmethod
    def query_matches(item: dict, query: str) -> bool:
        """The result must actually be the show we asked about — fuzzy search can
        return popular lookalikes (asking for 'Solo Leveling' ep 14 must not give
        'Secret Level' S01E14)."""
        norm = lambda t: re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()
        q = norm(query)
        if not q:
            return True
        attr = item.get("attributes") or {}
        fd = attr.get("feature_details") or {}
        hay = norm(" ".join(str(x) for x in (fd.get("title"), fd.get("parent_title"),
                                             fd.get("movie_name"), attr.get("release"), attr.get("slug"))))
        return q in hay

    @staticmethod
    def pick_best(results: List[dict], languages: List[str]) -> Optional[dict]:
        """Prefer exact hash matches, then earlier configured languages, then popularity."""
        def key(item):
            attr = item.get("attributes") or {}
            lang = (attr.get("language") or "").lower()
            lang_rank = languages.index(lang) if lang in languages else len(languages)
            return (not attr.get("moviehash_match"), lang_rank,
                    attr.get("hearing_impaired") or False, -(attr.get("download_count") or 0))
        files = [r for r in results if (r.get("attributes") or {}).get("files")]
        return min(files, key=key) if files else None

    def _filtered(self, results: List[dict], query: str) -> List[dict]:
        good = [r for r in results if self.query_matches(r, query)]
        return good or results   # nothing title-matched: keep the list, \u21bb can cycle it

    # ---- the whole flow -------------------------------------------------
    def find_and_download(self, video: dict, path: Path, exclude: Optional[set] = None) -> Dict:
        if not self.configured():
            raise SubsearchError("OpenSubtitles is not set up — add username, password and "
                                 "api_key under 'opensubtitles:' in config.yaml")
        with _lock:
            q = quota(self.daily_limit)
            if q["remaining"] <= 0:
                raise SubsearchError(f"Daily subtitle limit reached ({q['used']}/{q['limit']}) — resets at midnight UTC")

            langs = ",".join(self.languages)
            results = []
            h = oshash(path)
            if h:
                results = self._search({"moviehash": h, "languages": langs})
            matched_by = "hash"
            if not results:
                np = self.name_params(video)
                results = self._filtered(self._search({**np, "languages": langs}), np.get("query", ""))
                matched_by = "name"
            if not results and video.get("abs_episode"):
                # anime fallback: many shows are indexed by absolute episode number
                q = video.get("folder") or video.get("title") or ""
                results = self._filtered(self._search({"query": q, "languages": langs,
                                                       "episode_number": int(video["abs_episode"])}), q)
                matched_by = "absolute number"
            if exclude:
                results = [r for r in results
                           if not any(f.get("file_id") in exclude
                                      for f in (r.get("attributes") or {}).get("files") or [])]
            best = self.pick_best(results, self.languages)
            if not best:
                raise SubsearchError("No other subtitles found for this video" if exclude
                                     else "No subtitles found for this video")

            attr = best["attributes"]
            file_id = attr["files"][0]["file_id"]
            token = self._token()
            try:
                res = self.http("POST", f"{API}/download", self._headers(token), {"file_id": file_id})
            except urllib.error.HTTPError as e:
                if e.code == 406:
                    raise SubsearchError("OpenSubtitles download quota exhausted for today")
                raise SubsearchError(f"OpenSubtitles download error ({e.code})")
            link = res.get("link")
            if not link:
                raise SubsearchError("OpenSubtitles gave no download link")
            content = self.fetch(link)

            lang = (attr.get("language") or self.languages[0] or "en").lower()
            out = path.with_name(f"{path.stem}.{lang}.srt")
            out.write_bytes(content)

            st = _load_state()
            if st.get("date") != _today():
                st["date"], st["used"] = _today(), 0
            st["used"] = st.get("used", 0) + 1
            vid = str(video.get("id") or "")
            if vid:
                st.setdefault("chosen", {}).setdefault(vid, []).append(file_id)
            if res.get("remaining") is not None:
                st["server_remaining"] = res["remaining"]
            _save_state(st)
            q = quota(self.daily_limit)
        return {"ok": True, "file": out.name, "language": lang, "matched_by": matched_by,
                "release": attr.get("release") or "", "used": q["used"],
                "limit": q["limit"], "remaining": q["remaining"]}
