"""FastAPI app: the single-page UI, the JSON API, and range-capable video streaming."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import artwork, catalog, convert, db, library, metadata, netinfo, organize, remote, subs, subsearch, trickplay
from .config import STATIC_DIR, load_config
from .library import IOS_EXTENSIONS

log = logging.getLogger("matinee.app")

cfg = load_config()
converter = convert.get_converter(cfg)
remote_hub = remote.Remote()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init(cfg.profiles[0])
    count = library.refresh(cfg)
    library.start_rescan_thread(cfg)
    converter.start()
    trickplay.sweep_orphans()
    print()
    print(f"  Matinee  —  {count} video(s) in {cfg.library}")
    print("  On this computer: http://localhost:%d" % cfg.port)
    for url in netinfo.urls(cfg.port):
        print(f"  On your phone:    {url}")
    print("  (phone and computer must be on the same Wi-Fi / hotspot)")
    if not convert.available():
        print("  ffmpeg not found — MKV/AVI files will not be converted for iPhone (brew install ffmpeg)")
    print()
    yield


app = FastAPI(title="Matinee", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

MIME = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
}


class Progress(BaseModel):
    id: str
    position: float
    duration: Optional[float] = None
    profile: Optional[str] = None


def _profile(name: Optional[str]) -> str:
    """Valid profile name or the default (first in config)."""
    return name if name in cfg.profiles else cfg.profiles[0]


# ----------------------------------------------------------------- helpers

def serialize(v: dict) -> dict:
    duration = v.get("duration") or 0
    position = v.get("position") or 0
    percent = round(100 * position / duration, 1) if duration and position else 0
    parts = v["rel_path"].split("/")
    ios_ok = bool(v["ios_ok"]) if v.get("ios_ok") is not None else v["ext"] in IOS_EXTENSIONS
    status = v.get("convert_status")
    job = converter.current
    converting = bool(job and job.rel == v["rel_path"])
    return {
        "id": v["id"],
        "title": v["title"],
        "folder": v["folder"],
        "subfolder": "/".join(parts[1:-1]),   # e.g. "Season 01"
        "ext": v["ext"],
        "size": v["size"],
        "duration": duration or None,
        "position": position,
        "percent": percent,
        "finished": bool(v.get("finished")),
        "played_at": v.get("played_at"),
        "added_at": v.get("added_at"),
        "ios_ok": ios_ok,
        "sub_offset": v.get("sub_offset") or 0,
        "convert": None if ios_ok else {
            "status": "converting" if converting else (status or ("queued" if cfg.convert_for_iphone and convert.available() else "none")),
            "percent": round(job.percent, 1) if converting else 0,
            "error": v.get("convert_error"),
        },
    }


def _video_or_404(video_id: str, profile: Optional[str] = None) -> dict:
    video = db.get_video(video_id, profile)
    if not video:
        raise HTTPException(404, "Unknown video")
    return video


def _all_serialized(profile: Optional[str] = None) -> List[dict]:
    return [serialize(v) for v in db.all_videos(profile)]


# ------------------------------------------------------------------- pages

_index_cache = {"key": None, "html": ""}
JS_PARTS = STATIC_DIR / "js"


def _build_appjs() -> None:
    """static/app.js is generated: the real sources are the ordered files in
    static/js/ (one closure, concatenated). Rebuilt whenever a part changes."""
    parts = sorted(JS_PARTS.glob("*.js"))
    if not parts:
        return
    out = STATIC_DIR / "app.js"
    newest = max(p.stat().st_mtime_ns for p in parts)
    if out.exists() and out.stat().st_mtime_ns >= newest:
        return
    body = "".join(p.read_text(encoding="utf-8") for p in parts)
    out.write_text(
        "/* GENERATED from static/js/ — edit the numbered files there, not this one. */\n"
        "/* Matinee front end: hash router, home rows, player with resume + progress sync. */\n"
        '(() => {\n  "use strict";\n\n' + body + "})();\n",
        encoding="utf-8",
    )


@app.get("/", include_in_schema=False)
def index() -> Response:
    """index.html with ?v= hashes stamped from the current app.js/style.css, so
    edited assets are picked up on reload without touching index.html by hand."""
    import hashlib
    import re as _re
    _build_appjs()
    assets = [STATIC_DIR / "app.js", STATIC_DIR / "style.css"]
    key = tuple(p.stat().st_mtime_ns for p in assets + [STATIC_DIR / "index.html"])
    if _index_cache["key"] != key:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for p in assets:
            v = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
            html = _re.sub(rf"/static/{p.name}\?v=\w+", f"/static/{p.name}?v={v}", html)
        _index_cache.update(key=key, html=html)
    return HTMLResponse(_index_cache["html"], headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


# --------------------------------------------------------------------- API

def _home_rows(cat: Dict) -> List[Dict]:
    """Netflix-style rows: each series is ONE tile (grouped in a "TV Shows" row);
    movie folders keep a row each with one tile per film. Episodes never appear
    on the home page — they live on the series page."""
    rows: List[Dict] = []
    series = [catalog.series_entry(name, info["videos"]) for name, info in cat.items() if info["series"]]
    if series:
        rows.append({"name": "TV Shows", "kind": "entries", "items": series})
    for name, info in cat.items():
        if not info["series"]:
            rows.append({"name": name, "kind": "videos", "items": info["videos"]})
    return rows


@app.get("/api/home")
def home(p: str = "") -> Dict:
    profile = _profile(p)
    videos = _all_serialized(profile)
    cat = catalog.build(videos)

    cont = catalog.continue_row(cat)
    for v in cont:
        v["poster"] = artwork.poster_url(v["folder"]) or artwork.poster_url(v["title"])
    recent = catalog.recent_entries(videos)
    hero_row = db.last_played(profile)
    hero = serialize(hero_row) if hero_row else None
    kicker = "Continue watching" if hero and not hero["finished"] else "Last played"
    if hero and hero["finished"]:
        info = cat.get(hero["folder"])
        if info and info["series"]:
            nxt = catalog.next_up(info["videos"])          # finished an episode → feature the next one
            if nxt and nxt["id"] != hero["id"]:
                hero, kicker = nxt, "Up next"
        elif cont:
            hero, kicker = cont[0], "Continue watching"
    if hero is None and recent:
        # Nothing played yet: feature the newest series (its first episode) or movie.
        first = recent[0]
        hero = first["next"] if first["type"] == "series" else first["video"]
        kicker = first.get("flag") or "Recently added"

    rows = _home_rows(cat)
    series_row = next((r for r in rows if r["kind"] == "entries"), None)
    if series_row and len(series_row["items"]) >= 4:
        by_folder = {e["folder"]: e for e in series_row["items"]}
        gidx = metadata.genres_index(list(by_folder))
        genre_rows = sorted(((g, fs) for g, fs in gidx.items() if len(fs) >= 2),
                            key=lambda x: -len(x[1]))[:4]
        for g, fs in genre_rows:
            rows.append({"name": g, "kind": "entries", "items": [by_folder[f] for f in fs if f in by_folder]})
    _attach_posters(recent)
    for r in rows:
        if r["kind"] == "entries":
            _attach_posters(r["items"])
        else:
            for v in r["items"]:
                v["poster"] = artwork.poster_url(v["title"])
    for e in recent:
        if e.get("type") == "movie":
            e["video"]["poster"] = artwork.poster_url(e["video"]["title"])
    hero_poster = artwork.poster_url(hero["folder"]) or artwork.poster_url(hero["title"]) if hero else None
    return {
        "library_offline": not library.available(cfg),
        "hero_poster": hero_poster,
        "hero": hero,
        "hero_kicker": kicker if hero else None,
        "continue": cont,
        "recent": recent,
        "rows": rows,
        "count": len(videos),
        "library": str(cfg.library),
        "profile": profile,
        "converting": converter.current.rel if converter.current else None,
    }


@app.get("/api/show/{folder}")
def show(folder: str, p: str = "") -> Dict:
    folder = unquote(folder)
    videos = [v for v in _all_serialized(_profile(p)) if v["folder"] == folder]
    if not videos:
        raise HTTPException(404, "Unknown show")
    detail = catalog.show_detail(folder, videos)
    detail["poster"] = artwork.poster_url(folder)
    return metadata.enrich_show(detail)


@app.get("/api/video/{video_id}")
def video_detail(video_id: str, p: str = "") -> Dict:
    profile = _profile(p)
    video = _video_or_404(video_id, profile)
    data = serialize(video)
    data["subtitles"] = library.find_subtitle(cfg, video) is not None
    data["rel_path"] = video["rel_path"]
    same_folder = [v for v in _all_serialized(profile) if v["folder"] == data["folder"]]
    data["next"] = catalog.next_after(same_folder, video_id)
    data["series"] = catalog.is_series(same_folder)
    return data


@app.get("/api/search")
def search(q: str = "", p: str = "") -> Dict:
    q = q.strip()
    results = catalog.search(_all_serialized(_profile(p)), q) if q else []
    for e in results:
        if e.get("type") == "series":
            e["poster"] = artwork.poster_url(e["folder"])
        elif e.get("type") == "movie":
            e["video"]["poster"] = artwork.poster_url(e["video"]["title"])
        elif e.get("type") == "episode":
            e["video"]["poster"] = artwork.poster_url(e["video"]["folder"])
    return {"q": q, "results": results}


@app.post("/api/progress")
async def progress(request: Request) -> Dict:
    # Accept JSON from fetch() and text/plain from navigator.sendBeacon().
    body = await request.json()
    prog = Progress(**body)
    _video_or_404(prog.id)
    result = db.save_progress(prog.id, prog.position, prog.duration, _profile(prog.profile))
    if result.get("ignored"):
        # leave a trail: if sub-threshold saves ever arrive in bursts again, this says who sent them
        log.info("ignored sub-threshold progress %s pos=%.1f from %s (%s)", prog.id, prog.position,
                 request.client.host if request.client else "?", request.headers.get("user-agent", "")[:60])
    return result


@app.post("/api/progress/{video_id}/clear")
def progress_clear(video_id: str, p: str = "") -> Dict:
    db.clear_progress(video_id, _profile(p))
    return {"ok": True}


@app.get("/api/profiles")
def profiles() -> Dict:
    return {"profiles": cfg.profiles, "default": cfg.profiles[0]}


@app.post("/api/rescan")
def rescan() -> Dict:
    count = library.refresh(cfg)
    converter.poke()
    return {"count": count}


@app.get("/api/info")
def info() -> Dict:
    return {"urls": netinfo.urls(cfg.port), "library": str(cfg.library), "port": cfg.port,
            "ffmpeg": convert.available()}


# --------------------------------------------------------------- organize

class Move(BaseModel):
    src: str
    dst: str


class Moves(BaseModel):
    moves: List[Move]


@app.get("/api/organize")
def organize_plan() -> Dict:
    plans = organize.plan(cfg)
    return {
        "auto": cfg.auto_organize,
        "pending": [p.to_dict() for p in plans if p.changed],
        "tidy": sum(1 for p in plans if not p.changed),
        "moves": db.recent_moves(),
        "convert": converter.status(),
    }


@app.post("/api/organize/apply")
def organize_apply(body: Moves) -> Dict:
    results = organize.apply(cfg, [m.dict() for m in body.moves])
    library.scan(cfg)
    return {"results": results}


@app.post("/api/organize/undo/{move_id}")
def organize_undo(move_id: int) -> Dict:
    result = organize.undo(cfg, move_id)
    library.scan(cfg)
    return result


@app.get("/api/organize/preview")
def organize_preview(name: str) -> Dict:
    """What would this file name become? (used for the try-it box on the Organize page)"""
    return organize.propose(name.strip("/")).to_dict()


# ---------------------------------------------------------------- convert

@app.get("/api/convert")
def convert_status() -> Dict:
    return converter.status()


@app.post("/api/convert/retry")
async def convert_retry(request: Request) -> Dict:
    body = await request.json()
    converter.retry(str(body.get("rel_path", "")))
    return {"ok": True}


@app.post("/api/convert/delete-originals")
def convert_delete_originals() -> Dict:
    return {"freed": converter.delete_originals()}


# ------------------------------------------------------------ trickplay

@app.get("/api/trickplay/{video_id}")
def trickplay_meta(video_id: str) -> Dict:
    meta = trickplay.get_meta(video_id)
    if not meta:
        raise HTTPException(404, "No preview sheet yet")
    return meta


@app.get("/trickplay/{video_id}.jpg")
def trickplay_sheet(video_id: str) -> Response:
    path = trickplay.sheet_path(video_id)
    if not path.is_file():
        raise HTTPException(404, "No preview sheet")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


# ------------------------------------------------------------- artwork

def _attach_posters(entries: List[Dict]) -> None:
    for e in entries:
        if e.get("type") == "series":
            e["poster"] = artwork.poster_url(e["folder"])


@app.get("/api/artwork/overview")
def artwork_overview() -> Dict:
    """Every series folder with its current poster (if any) — for the Artwork page."""
    cat = catalog.build(_all_serialized())
    shows = [
        {"folder": name, "count": len(info["videos"]), "poster": artwork.poster_url(name)}
        for name, info in cat.items() if info["series"]
    ]
    shows.sort(key=lambda x: (x["poster"] is not None, x["folder"].lower()))
    movies = [
        {"folder": v["title"], "poster": artwork.poster_url(v["title"])}
        for name, info in cat.items() if not info["series"] for v in info["videos"]
    ]
    movies.sort(key=lambda x: (x["poster"] is not None, x["folder"].lower()))
    return {"shows": shows, "movies": movies}


@app.get("/api/artwork/search")
def artwork_search(folder: str, kind: str = "series") -> Dict:
    return {"folder": folder, "candidates": artwork.search(folder, kind)}


@app.post("/api/artwork/set")
async def artwork_set(request: Request) -> Dict:
    body = await request.json()
    folder, url = str(body.get("folder", "")), str(body.get("url", ""))
    if not folder or not url:
        raise HTTPException(400, "folder and url required")
    try:
        result = {"poster": artwork.set_poster(folder, url)}
        if body.get("tvmaze_id"):
            metadata.pin_show(folder, int(body["tvmaze_id"]))   # descriptions follow the approved poster
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(502, "Could not download the image")


@app.post("/api/artwork/remove")
async def artwork_remove(request: Request) -> Dict:
    body = await request.json()
    artwork.remove(str(body.get("folder", "")))
    return {"ok": True}


@app.get("/poster/{folder:path}")
def poster(folder: str) -> Response:
    path = artwork.poster_path(unquote(folder))
    if not path.is_file():
        raise HTTPException(404, "No poster")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=60"})


# ---------------------------------------------------------- activity feed

@app.get("/api/setup")
def setup_state() -> Dict:
    from . import config as configmod
    return {
        "first_run": not configmod.CONFIG_PATH.exists(),
        "app_name": cfg.app_name,
        "skip_seconds": cfg.skip_seconds,
        "profiles": cfg.profiles,
        "convert_for_iphone": cfg.convert_for_iphone,
        "library": str(cfg.library),
    }


@app.post("/api/setup")
async def setup_save(request: Request) -> Dict:
    """First-run wizard: write a fresh config.yaml and apply it without a restart."""
    from . import config as configmod
    body = await request.json()
    name = str(body.get("app_name") or "Matinee").strip()[:24] or "Matinee"
    profiles = [str(p).strip() for p in (body.get("profiles") or []) if str(p).strip()][:8] or ["You"]
    profiles = list(dict.fromkeys(profiles))
    convert_on = bool(body.get("convert_for_iphone", True))
    skip = max(5, min(120, int(body.get("skip_seconds") or 30)))
    lib = body.get("library")
    if lib:
        p = Path(str(lib)).expanduser()
        if not p.is_dir():
            raise HTTPException(422, f"{p} is not a folder")
        cfg.library = p
    text = (
        f"# {name} settings — see config.example.yaml for every option.\n"
        f"app_name: {name}\n"
        f"library: {cfg.library}\n"
        f"profiles: [{', '.join(profiles)}]\n"
        f"convert_for_iphone: {'true' if convert_on else 'false'}\n"
        f"skip_seconds: {skip}\n"
    )
    configmod.CONFIG_PATH.write_text(text, encoding="utf-8")
    cfg.app_name, cfg.profiles, cfg.convert_for_iphone, cfg.skip_seconds = name, profiles, convert_on, skip
    db.init(profiles[0])
    count = library.scan(cfg)
    return {"ok": True, "count": count}


@app.get("/api/browse")
def browse(path: str = "") -> Dict:
    """Server-side folder listing for the library picker. Folders only, plus a
    count of playable files directly inside — enough to pick a library root."""
    home = Path.home()
    p = Path(path).expanduser() if path else home
    if not p.is_dir():
        raise HTTPException(404, f"{p} is not a folder")
    exts = set(cfg.extensions)
    dirs: List[Dict] = []
    media = 0
    denied = False
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child)})
                elif child.suffix.lower().lstrip(".") in exts:
                    media += 1
            except OSError:
                continue
    except PermissionError:
        denied = True   # macOS folder protection: the server wasn't granted access
    shortcuts = [{"name": "Home", "path": str(home)}]
    for name in ("Downloads", "Movies", "Desktop", "Documents"):
        q = home / name
        if q.is_dir():
            shortcuts.append({"name": name, "path": str(q)})
    volumes = Path("/Volumes")
    if volumes.is_dir():
        try:
            for v in sorted(volumes.iterdir()):
                if v.is_dir() and not v.name.startswith("."):
                    shortcuts.append({"name": "\U0001f4be " + v.name, "path": str(v)})
        except OSError:
            pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None,
            "dirs": dirs[:200], "media_count": media, "denied": denied, "shortcuts": shortcuts}


@app.post("/api/library")
async def set_library(request: Request) -> Dict:
    """Point the app at a different library folder (e.g. after moving to a disk).
    Rewrites just the `library:` line so config.yaml comments survive."""
    body = await request.json()
    p = Path(str(body.get("path") or "").strip()).expanduser()
    if not p.is_dir():
        raise HTTPException(422, f"{p} is not a folder (is the disk connected?)")
    from .config import CONFIG_PATH
    import re as _re
    text = CONFIG_PATH.read_text(encoding="utf-8")
    line = f"library: {p}"
    text, n = _re.subn(r"(?m)^library:.*$", line, text)
    if not n:
        text = line + "\n" + text
    CONFIG_PATH.write_text(text, encoding="utf-8")
    cfg.library = p
    count = library.scan(cfg)
    return {"ok": True, "library": str(p), "count": count}


@app.get("/api/activity")
def activity() -> Dict:
    """Cheap status poll for the UI chip: what organize/convert is doing right now."""
    job = converter.current
    convs = db.conversions()
    now = time.time()
    recent = [m for m in db.recent_moves(30) if now - m["at"] < 600 and not m["undone"]]
    free = converter.free_bytes()
    try:
        pending = sum(1 for p in organize.plan(cfg) if p.changed)
    except Exception:
        pending = 0
    return {
        "library_offline": not library.available(cfg),
        "pending_moves": pending,
        "failed_count": sum(1 for c in convs if c["status"] == "failed"),
        "originals_gb": round(converter.originals_size() / 2**30, 1),
        "low_disk": free < convert.MIN_FREE_BYTES,
        "free_gb": round(free / 2**30, 1),
        "converting": {
            "rel_path": job.rel, "percent": round(job.percent, 1), "stage": job.stage,
        } if job else None,
        "queued": sum(1 for c in convs if c["status"] == "queued"),
        "failed": sum(1 for c in convs if c["status"] == "failed"),
        "recent_moves": [{"src": m["src"], "dst": m["dst"], "at": m["at"], "kind": m["kind"]} for m in recent[:6]],
        "recent_count": len(recent),
    }


# ---------------------------------------------------------- remote play

@app.post("/api/remote/state")
async def remote_state(request: Request) -> Dict:
    """The playing device (laptop) reports its state and picks up queued commands."""
    body = await request.json()
    sid = str(body.get("screen_id", ""))[:64]
    if not sid:
        raise HTTPException(400, "screen_id required")
    return {"commands": remote_hub.report(sid, body.get("name"), body.get("state"))}


@app.get("/api/remote/screens")
def remote_screens() -> Dict:
    return {"screens": remote_hub.screens()}


@app.post("/api/remote/command")
async def remote_command(request: Request) -> Dict:
    body = await request.json()
    sid = str(body.get("screen_id", ""))[:64]
    command = body.get("command") or {}
    if not sid or not isinstance(command, dict) or not command.get("action"):
        raise HTTPException(400, "screen_id and command.action required")
    return {"ok": remote_hub.send(sid, command)}


# ------------------------------------------------------------------ media

@app.api_route("/stream/{video_id}", methods=["GET", "HEAD"])
def stream(video_id: str) -> Response:
    video = _video_or_404(video_id)
    path = library.resolve_path(cfg, video)
    if path is None:
        raise HTTPException(404, "File not found on disk")
    # Starlette's FileResponse answers Range requests (206 + Content-Range),
    # which iPhone Safari requires before it will play anything.
    return FileResponse(
        path,
        media_type=MIME.get(video["ext"], "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
    )


def _osub() -> subsearch.OpenSubtitles:
    # Re-read the block on every call: editing credentials in config.yaml
    # takes effect immediately, no server restart needed.
    from .config import load_config
    return subsearch.OpenSubtitles(load_config().opensubtitles)


@app.get("/api/movie/{video_id}")
def movie_detail(video_id: str, p: str = "", fast: int = 0) -> Dict:
    """Everything the movie page needs: video + poster + Wikipedia info + subtitle status.
    fast=1 answers from caches only (instant); the page then asks again without it."""
    profile = _profile(p)
    video = _video_or_404(video_id, profile)
    data = serialize(video)
    data["subtitles"] = library.find_subtitle(cfg, video) is not None
    data["poster"] = artwork.poster_url(video["title"]) or artwork.poster_url(video["folder"])
    data["info"] = metadata.movie_info(video["title"], fetch=not fast)
    osub = subsearch.OpenSubtitles(cfg.opensubtitles)
    data["subsearch"] = {"configured": osub.configured(), **subsearch.quota(osub.daily_limit)}
    return data


@app.get("/api/subsearch/status")
def subsearch_status() -> Dict:
    osub = _osub()
    return {"configured": osub.configured(), **subsearch.quota(osub.daily_limit)}


@app.post("/api/video/{video_id}/suboffset")
async def video_suboffset(video_id: str, request: Request) -> Dict:
    """Subtitle sync nudge: positive shows cues later, negative earlier."""
    _video_or_404(video_id)
    body = await request.json()
    offset = max(-600.0, min(600.0, float(body.get("offset") or 0)))
    db.set_sub_offset(video_id, offset)
    return {"ok": True, "offset": offset}


@app.post("/api/subsearch/{video_id}")
def subsearch_video(video_id: str, replace: int = 0) -> Dict:
    """Find subtitles on OpenSubtitles and save them as a sidecar next to the file.
    replace=1 fetches the next-best release instead (when the timing is off)."""
    video = _video_or_404(video_id)
    path = library.resolve_path(cfg, video)
    if path is None:
        raise HTTPException(404, "File not found on disk")
    if not replace and library.find_subtitle(cfg, video) is not None:
        return {"ok": True, "already": True}
    exclude = subsearch.chosen_ids(video_id) if replace else set()
    # absolute episode number across seasons (S02E02 of a 12-episode S01 -> 14),
    # used as a last-resort search for anime indexed without seasons
    video = dict(video)
    same = [serialize(x) for x in db.all_videos() if x["folder"] == video["folder"]]
    if catalog.is_series(same):
        ordered = [x["id"] for x in catalog._order(same) if catalog.episode_key(x)]
        if video_id in ordered:
            video["abs_episode"] = ordered.index(video_id) + 1
    try:
        result = _osub().find_and_download(video, path, exclude=exclude)
    except subsearch.SubsearchError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"OpenSubtitles request failed: {e}")
    if replace:
        # the old download and any sync nudge belong to the previous file
        for old_side in list(path.parent.glob(f"{path.stem}*.srt")) + list(path.parent.glob(f"{path.stem}*.vtt")):
            if old_side.name != result["file"]:
                old_side.unlink(missing_ok=True)
        db.set_sub_offset(video_id, 0)
    return result





@app.get("/subs/{video_id}.vtt")
def subtitles(video_id: str) -> Response:
    video = _video_or_404(video_id)
    path = library.find_subtitle(cfg, video)
    if path is None:
        raise HTTPException(404, "No subtitles")
    vtt = subs.shift(subs.to_vtt(path), video.get("sub_offset") or 0)
    return PlainTextResponse(vtt, media_type="text/vtt; charset=utf-8")
