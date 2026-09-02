"""Scan the library folder into the videos table, and keep it fresh in the background."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

from . import db
from .config import Config, DATA_DIR

log = logging.getLogger("matinee.library")

ROOT_ROW_NAME = "Movies"  # row for files dropped directly into the library folder
IOS_EXTENSIONS = {"mp4", "m4v", "mov"}  # containers iPhone Safari plays natively


def video_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


def available(cfg: Config) -> bool:
    """False when the library folder is gone — e.g. the external disk is unplugged."""
    return cfg.library.is_dir()


def scan(cfg: Config) -> int:
    if not available(cfg):
        log.warning("library %s is not reachable — skipping scan", cfg.library)
        return 0
    exts = set(cfg.extensions)
    found: List[dict] = []
    for dirpath, dirnames, filenames in os.walk(cfg.library):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if name.startswith(".") or name.startswith("._"):
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in exts:
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            rel = full.relative_to(cfg.library).as_posix()
            parts = rel.split("/")
            folder = parts[0] if len(parts) > 1 else ROOT_ROW_NAME
            found.append(
                {
                    "id": video_id(rel),
                    "rel_path": rel,
                    "title": name[: -(len(ext) + 1)] if ext else name,
                    "folder": folder,
                    "ext": ext,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
    db.upsert_videos(found)
    if found:
        removed = db.delete_videos_not_in(v["rel_path"] for v in found)
        if removed:
            log.info("removed %d missing video(s)", removed)
    elif db.count_videos():
        # an empty scan while the catalogue has entries smells like an unmounted
        # disk, not a deliberately emptied library — keep everything
        log.warning("library scan found nothing — keeping the existing catalogue")
    return len(found)


def maybe_backup_db(cfg: Config) -> None:
    """When the library lives on another disk, keep a daily copy of the watch-history
    database next to the videos — the disk then carries its own metadata, so the
    collection plus history could be restored on any machine."""
    try:
        if not available(cfg):
            return
        if os.stat(cfg.library).st_dev == os.stat(DATA_DIR).st_dev:
            return   # same disk as data/ — a copy there adds nothing
        dest_dir = cfg.library / ".matinee-backup"
        dest = dest_dir / "matinee.db"
        if dest.exists() and time.time() - dest.stat().st_mtime < 86400:
            return
        import sqlite3
        dest_dir.mkdir(exist_ok=True)
        src = sqlite3.connect(db.DB_PATH)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        (dest_dir / "README.txt").write_text(
            "Daily backup of the Matinee watch history / catalogue database.\n"
            "To restore on a (new) Mac: copy matinee.db into the app's data/ folder.\n")
        log.info("backed up catalogue db to %s", dest)
    except Exception as e:
        log.warning("db backup skipped: %s", e)


def resolve_path(cfg: Config, video: dict) -> Optional[Path]:
    """Absolute path for a catalogue row, or None if the file vanished."""
    path = (cfg.library / video["rel_path"]).resolve()
    try:
        path.relative_to(cfg.library.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def find_subtitle(cfg: Config, video: dict) -> Optional[Path]:
    """Sidecar subtitle next to the video: <name>.srt / <name>.vtt / <name>.en.srt ..."""
    path = cfg.library / video["rel_path"]
    stem = path.name[: -(len(video["ext"]) + 1)]
    for candidate in sorted(path.parent.glob(f"{stem}*.srt")) + sorted(path.parent.glob(f"{stem}*.vtt")):
        if candidate.is_file():
            return candidate
    return None


def refresh(cfg: Config) -> int:
    """One maintenance pass: scan, tidy up anything new (if enabled), scan again if files moved."""
    count = scan(cfg)
    if cfg.auto_organize:
        from . import organize  # imported here to avoid a cycle at module load

        moved = [r for r in organize.auto_organize(cfg) if r["ok"]]
        if moved:
            count = scan(cfg)
    from . import convert

    convert.get_converter(cfg).poke()   # probe/convert anything new
    maybe_backup_db(cfg)
    return count


def start_rescan_thread(cfg: Config) -> threading.Thread:
    def loop() -> None:
        while True:
            time.sleep(cfg.rescan_seconds)
            try:
                refresh(cfg)
            except Exception:  # keep the loop alive no matter what
                log.exception("rescan failed")

    t = threading.Thread(target=loop, name="rescan", daemon=True)
    t.start()
    return t
