"""SQLite storage: the video catalogue and per-video watch progress."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional

from .config import DB_PATH

DEFAULT_PROFILE = "You"  # overwritten by init() with the first profile from config
FINISHED_RATIO = 0.90   # past this point a video counts as watched (credits territory)
MIN_RESUME_SECONDS = 5  # below this we just start from the beginning

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id        TEXT PRIMARY KEY,
    rel_path  TEXT UNIQUE NOT NULL,
    title     TEXT NOT NULL,
    folder    TEXT NOT NULL,
    ext       TEXT NOT NULL,
    size      INTEGER NOT NULL,
    mtime     REAL NOT NULL,
    added_at  REAL NOT NULL,
    duration  REAL,
    ios_ok    INTEGER,
    probed_at REAL
);
CREATE TABLE IF NOT EXISTS progress (
    video_id   TEXT NOT NULL,
    profile    TEXT NOT NULL DEFAULT 'You',
    position   REAL NOT NULL,
    duration   REAL,
    updated_at REAL NOT NULL,
    finished   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (video_id, profile)
);
CREATE TABLE IF NOT EXISTS moves (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    src     TEXT NOT NULL,
    dst     TEXT NOT NULL,
    at      REAL NOT NULL,
    auto    INTEGER NOT NULL DEFAULT 0,
    undone  INTEGER NOT NULL DEFAULT 0,
    kind    TEXT NOT NULL DEFAULT 'move'
);
CREATE TABLE IF NOT EXISTS conversions (
    rel_path   TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    error      TEXT,
    output     TEXT,
    updated_at REAL NOT NULL
);
"""

# Columns added after the first release; applied to existing databases on start.
MIGRATIONS = [
    "ALTER TABLE videos ADD COLUMN ios_ok INTEGER",
    "ALTER TABLE videos ADD COLUMN probed_at REAL",
    "ALTER TABLE moves ADD COLUMN kind TEXT NOT NULL DEFAULT 'move'",
    "ALTER TABLE videos ADD COLUMN sub_offset REAL NOT NULL DEFAULT 0",
]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(default_profile: str = "You") -> None:
    global DEFAULT_PROFILE
    # one-time migration from the app's old name: keep everyone's watch history
    legacy = DB_PATH.parent / "shaggyflix.db"
    if not DB_PATH.exists() and legacy.exists():
        for suffix in ("", "-wal", "-shm"):
            src = legacy.parent / (legacy.name + suffix)
            if src.exists():
                src.rename(DB_PATH.parent / (DB_PATH.name + suffix))
    DEFAULT_PROFILE = default_profile
    with connect() as conn:
        # migrate a pre-profile progress table (video_id was the whole key)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(progress)")]
        if cols and "profile" not in cols:
            conn.execute("ALTER TABLE progress RENAME TO progress_old")
        conn.executescript(SCHEMA)
        if cols and "profile" not in cols:
            conn.execute(
                "INSERT INTO progress (video_id, profile, position, duration, updated_at, finished) "
                "SELECT video_id, ?, position, duration, updated_at, finished FROM progress_old",
                (default_profile,),
            )
            conn.execute("DROP TABLE progress_old")
        conn.execute("PRAGMA journal_mode=WAL")
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already there


# ---------------------------------------------------------------- videos

def upsert_videos(rows: Iterable[dict]) -> None:
    now = time.time()
    with connect() as conn:
        for r in rows:
            # A replaced file (new size) gets a fresh start: re-probe it and forget
            # any old conversion verdict, so a re-downloaded episode converts again.
            old = conn.execute("SELECT size FROM videos WHERE rel_path = ?", (r["rel_path"],)).fetchone()
            if old and old["size"] != r["size"]:
                conn.execute("DELETE FROM conversions WHERE rel_path = ?", (r["rel_path"],))
                conn.execute("UPDATE videos SET probed_at = NULL, ios_ok = NULL WHERE rel_path = ?", (r["rel_path"],))
            conn.execute(
                """INSERT INTO videos (id, rel_path, title, folder, ext, size, mtime, added_at)
                   VALUES (:id, :rel_path, :title, :folder, :ext, :size, :mtime, :added_at)
                   ON CONFLICT(rel_path) DO UPDATE SET
                       title=excluded.title, folder=excluded.folder, ext=excluded.ext,
                       size=excluded.size, mtime=excluded.mtime""",
                {**r, "added_at": now},
            )
        # conversion records for files that no longer exist are just clutter
        conn.execute("DELETE FROM conversions WHERE rel_path NOT IN (SELECT rel_path FROM videos)")


def delete_videos_not_in(rel_paths: Iterable[str]) -> int:
    keep = set(rel_paths)
    with connect() as conn:
        existing = [row["rel_path"] for row in conn.execute("SELECT rel_path FROM videos")]
        gone = [p for p in existing if p not in keep]
        conn.executemany("DELETE FROM videos WHERE rel_path = ?", [(p,) for p in gone])
    return len(gone)


def count_videos() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]


def update_file_stats(rel_path: str, size: int, mtime: float) -> None:
    with connect() as conn:
        conn.execute("UPDATE videos SET size = ?, mtime = ? WHERE rel_path = ?", (size, mtime, rel_path))


def set_duration(video_id: str, duration: float) -> None:
    with connect() as conn:
        conn.execute("UPDATE videos SET duration = ? WHERE id = ?", (duration, video_id))


def get_video_by_path(rel_path: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE rel_path = ?", (rel_path,)).fetchone()
        return dict(row) if row else None


def move_video(src_rel: str, dst_rel: str) -> None:
    """A file was renamed/moved on disk: carry its catalogue row and watch progress over."""
    import hashlib  # local import keeps db free of library's helpers

    old_id = hashlib.sha1(src_rel.encode("utf-8")).hexdigest()[:16]
    new_id = hashlib.sha1(dst_rel.encode("utf-8")).hexdigest()[:16]
    name = dst_rel.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    title = name[: -(len(ext) + 1)] if ext else name
    parts = dst_rel.split("/")
    folder = parts[0] if len(parts) > 1 else "Movies"
    with connect() as conn:
        conn.execute("DELETE FROM videos WHERE id = ? AND rel_path != ?", (new_id, src_rel))
        conn.execute(
            "UPDATE videos SET id = ?, rel_path = ?, title = ?, folder = ?, ext = ? WHERE id = ?",
            (new_id, dst_rel, title, folder, ext, old_id),
        )
        conn.execute("DELETE FROM progress WHERE video_id = ?", (new_id,))
        conn.execute("UPDATE progress SET video_id = ? WHERE video_id = ?", (new_id, old_id))


# ------------------------------------------------------ probing / converting

def videos_needing_probe(limit: int = 25) -> List[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, rel_path, ext FROM videos WHERE probed_at IS NULL ORDER BY added_at DESC LIMIT ?", (limit,))]


def set_probe(video_id: str, duration: Optional[float], ios_ok: Optional[bool]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE videos SET duration = COALESCE(?, duration), ios_ok = ?, probed_at = ? WHERE id = ?",
            (duration, None if ios_ok is None else int(ios_ok), time.time(), video_id),
        )


def videos_not_ios_ok() -> List[dict]:
    """Probed files iPhone can't play, oldest first so a series converts in order."""
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM videos WHERE ios_ok = 0 ORDER BY rel_path COLLATE NOCASE")]


def get_conversion(rel_path: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM conversions WHERE rel_path = ?", (rel_path,)).fetchone()
        return dict(row) if row else None


def set_conversion(rel_path: str, status: str, error: Optional[str] = None, output: Optional[str] = None) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO conversions (rel_path, status, error, output, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET status=excluded.status, error=excluded.error,
                   output=excluded.output, updated_at=excluded.updated_at""",
            (rel_path, status, error, output, time.time()),
        )


def clear_conversion(rel_path: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM conversions WHERE rel_path = ?", (rel_path,))


def reset_stale_conversions() -> int:
    """A previous run died mid-conversion: put those back in the queue."""
    with connect() as conn:
        cur = conn.execute("UPDATE conversions SET status = 'queued' WHERE status = 'converting'")
        return cur.rowcount


def conversions() -> List[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM conversions ORDER BY updated_at DESC")]


# ----------------------------------------------------------------- moves log

def log_move(src: str, dst: str, auto: bool, kind: str = "move") -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO moves (src, dst, at, auto, kind) VALUES (?, ?, ?, ?, ?)",
                           (src, dst, time.time(), int(auto), kind))
        return int(cur.lastrowid)


def recent_moves(limit: int = 100) -> List[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM moves ORDER BY id DESC LIMIT ?", (limit,))]


def get_move(move_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM moves WHERE id = ?", (move_id,)).fetchone()
        return dict(row) if row else None


def mark_undone(move_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE moves SET undone = 1 WHERE id = ?", (move_id,))


def undone_pairs() -> set:
    """(src, dst) pairs the user has reverted — the auto-organizer must not redo them."""
    with connect() as conn:
        return {(r["src"], r["dst"]) for r in conn.execute("SELECT src, dst FROM moves WHERE undone = 1")}


VIDEO_SELECT = """
SELECT v.id, v.rel_path, v.title, v.folder, v.ext, v.size, v.mtime, v.added_at, v.ios_ok, v.sub_offset,
       COALESCE(p.duration, v.duration) AS duration,
       p.position, p.updated_at AS played_at, p.finished,
       c.status AS convert_status, c.error AS convert_error
FROM videos v LEFT JOIN progress p ON p.video_id = v.id AND p.profile = ?
              LEFT JOIN conversions c ON c.rel_path = v.rel_path
"""


def all_videos(profile: Optional[str] = None) -> List[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            VIDEO_SELECT + " ORDER BY v.folder, v.title COLLATE NOCASE", (profile or DEFAULT_PROFILE,))]


def get_video(video_id: str, profile: Optional[str] = None) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(VIDEO_SELECT + " WHERE v.id = ?", (profile or DEFAULT_PROFILE, video_id)).fetchone()
        return dict(row) if row else None


def search_videos(query: str, limit: int = 60, profile: Optional[str] = None) -> List[dict]:
    like = f"%{query.strip()}%"
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                VIDEO_SELECT + " WHERE v.title LIKE ? OR v.folder LIKE ? "
                "ORDER BY v.title COLLATE NOCASE LIMIT ?",
                (profile or DEFAULT_PROFILE, like, like, limit),
            )
        ]


# -------------------------------------------------------------- progress

def save_progress(video_id: str, position: float, duration: Optional[float],
                  profile: Optional[str] = None) -> Dict:
    position = max(0.0, float(position))
    finished = bool(duration and duration > 0 and position / duration >= FINISHED_RATIO)
    if position < MIN_RESUME_SECONDS and not finished:
        # A position under the resume threshold says nothing about what was watched,
        # but persisting it would overwrite real progress, un-finish an episode and
        # make it the series' "last touched" — bursts of these (stray tabs parked at
        # 0:00) silently wrecked Continue Watching. Ignore them entirely.
        return {"id": video_id, "position": position, "finished": False, "ignored": True}
    with connect() as conn:
        conn.execute(
            """INSERT INTO progress (video_id, profile, position, duration, updated_at, finished)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(video_id, profile) DO UPDATE SET
                   position=excluded.position,
                   duration=COALESCE(excluded.duration, progress.duration),
                   updated_at=excluded.updated_at,
                   finished=excluded.finished""",
            (video_id, profile or DEFAULT_PROFILE, position, duration, time.time(), int(finished)),
        )
        if duration:
            conn.execute("UPDATE videos SET duration = ? WHERE id = ?", (duration, video_id))
    return {"id": video_id, "position": position, "finished": finished}


def clear_progress(video_id: str, profile: Optional[str] = None) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM progress WHERE video_id = ? AND profile = ?",
                     (video_id, profile or DEFAULT_PROFILE))


def set_sub_offset(video_id: str, offset: float) -> None:
    with connect() as conn:
        conn.execute("UPDATE videos SET sub_offset = ? WHERE id = ?", (offset, video_id))


def continue_watching(limit: int = 20, profile: Optional[str] = None) -> List[dict]:
    """Videos that were started but not finished, most recently played first."""
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                VIDEO_SELECT + " WHERE p.position > ? AND p.finished = 0 "
                "ORDER BY p.updated_at DESC LIMIT ?",
                (profile or DEFAULT_PROFILE, MIN_RESUME_SECONDS, limit),
            )
        ]


def last_played(profile: Optional[str] = None) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            VIDEO_SELECT + " WHERE p.updated_at IS NOT NULL ORDER BY p.updated_at DESC LIMIT 1",
            (profile or DEFAULT_PROFILE,),
        ).fetchone()
        return dict(row) if row else None


def recently_added(limit: int = 20, profile: Optional[str] = None) -> List[dict]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(VIDEO_SELECT + " ORDER BY v.added_at DESC, v.mtime DESC LIMIT ?",
                                  (profile or DEFAULT_PROFILE, limit))
        ]
