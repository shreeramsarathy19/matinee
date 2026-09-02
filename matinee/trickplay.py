"""Scrubber previews: a sprite sheet of tiny frames per video, generated in the
background with ffmpeg. The player shows the right tile while you drag the seek
bar, like Netflix."""
from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional

from . import convert, db
from .config import DATA_DIR, Config

log = logging.getLogger("matinee.trickplay")

TRICK_DIR = DATA_DIR / "trickplay"
THUMB_W = 176          # tile width (height follows the aspect)
COLS = 10
MAX_TILES = 320        # cap the sheet size; interval grows with duration


def sheet_path(video_id: str) -> Path:
    return TRICK_DIR / f"{video_id}.jpg"


def meta_path(video_id: str) -> Path:
    return TRICK_DIR / f"{video_id}.json"


def get_meta(video_id: str) -> Optional[Dict]:
    try:
        return json.loads(meta_path(video_id).read_text())
    except Exception:
        return None


def generate(cfg: Config, video: dict) -> bool:
    """Build the sprite sheet for one catalogue row. Returns True on success."""
    src = cfg.library / video["rel_path"]
    duration = video.get("duration") or 0
    if not src.is_file() or duration < 30:
        return False
    interval = max(5, math.ceil(duration / MAX_TILES))
    count = max(1, math.floor(duration / interval))
    rows = math.ceil(count / COLS)
    TRICK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = sheet_path(video["id"]).with_suffix(".tmp.jpg")
    cmd = [
        convert.ffmpeg_path(), "-y", "-nostdin", "-loglevel", "error",
        "-skip_frame", "nokey", "-i", str(src),
        "-vf", f"fps=1/{interval},scale={THUMB_W}:-2,tile={COLS}x{rows}",
        "-frames:v", "1", "-q:v", "5", str(tmp),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           preexec_fn=lambda: os.nice(15))
        if r.returncode != 0 or not tmp.is_file():
            # some files dislike keyframe-skipping; retry decoding everything
            cmd.remove("-skip_frame"); cmd.remove("nokey")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                               preexec_fn=lambda: os.nice(15))
            if r.returncode != 0 or not tmp.is_file():
                log.info("trickplay failed for %s: %s", video["rel_path"], (r.stderr or "")[-120:])
                return False
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        log.info("trickplay failed for %s: %s", video["rel_path"], e)
        tmp.unlink(missing_ok=True)
        return False
    # tile height from the sheet's real dimensions
    probe = convert.probe(tmp)
    stream = next((s for s in (probe or {}).get("streams", []) if s.get("codec_type") == "video"), {})
    tile_h = int(stream.get("height", 0) / rows) if rows else 0
    os.replace(tmp, sheet_path(video["id"]))
    meta_path(video["id"]).write_text(json.dumps({
        "interval": interval, "cols": COLS, "rows": rows, "count": count,
        "tile_w": int(stream.get("width", THUMB_W * COLS) / COLS), "tile_h": tile_h,
        "duration": duration,
    }))
    return True


def next_missing(order_desc_added: list) -> Optional[dict]:
    """First catalogue row (newest first) that has a duration but no sheet yet."""
    for v in order_desc_added:
        if (v.get("duration") or 0) >= 30 and not meta_path(v["id"]).is_file():
            return v
    return None


def sweep_orphans() -> None:
    """Drop sheets whose video is gone from the catalogue."""
    if not TRICK_DIR.exists():
        return
    ids = {v["id"] for v in db.all_videos()}
    for f in TRICK_DIR.glob("*.json"):
        if f.stem not in ids:
            f.unlink(missing_ok=True)
            sheet_path(f.stem).unlink(missing_ok=True)
