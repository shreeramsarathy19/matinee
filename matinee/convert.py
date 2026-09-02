"""Make videos playable on iPhone.

iPhone Safari only plays MP4/MOV containers with H.264 or HEVC video and AAC/MP3
audio. Most MKV downloads already contain exactly that, so they just need
re-wrapping (`ffmpeg -c copy`, seconds per file). Anything else (AC3/DTS audio,
10-bit or VP9/AV1 video, AVI) is transcoded — slower, but automatic.

A background worker converts one file at a time. The original is parked in
`library/.originals/` (or deleted, see `keep_originals`) and the new MP4 takes
its place, inheriting watch progress.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import db
from .config import Config

log = logging.getLogger("matinee.convert")

ORIGINALS_DIR = ".originals"
IOS_CONTAINERS = {"mp4", "m4v", "mov"}
IOS_VIDEO = {"h264", "hevc"}
IOS_AUDIO = {"aac", "mp3", "alac"}
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt"}
STABLE_SECONDS = 60
MIN_FREE_BYTES = 15 * 2**30   # pause converting below this much free disk


def ffmpeg_path() -> Optional[str]:
    for name in ("ffmpeg",):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.exists(p):
            return p
    return None


def ffprobe_path() -> Optional[str]:
    p = shutil.which("ffprobe")
    if p:
        return p
    for p in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if os.path.exists(p):
            return p
    return None


def available() -> bool:
    return bool(ffmpeg_path() and ffprobe_path())


# ------------------------------------------------------------------ probing

def probe(path: Path) -> Optional[Dict]:
    exe = ffprobe_path()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def analyse(info: Dict, ext: str) -> Dict:
    """What needs to happen to make this file iPhone-friendly?"""
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) != 1), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    duration = float(info.get("format", {}).get("duration") or 0) or None

    vcodec = (video or {}).get("codec_name", "")
    pix_fmt = (video or {}).get("pix_fmt", "") or ""
    height = int((video or {}).get("height") or 0)
    video_ok = vcodec in IOS_VIDEO and (pix_fmt in ("", "yuv420p", "yuvj420p") or (vcodec == "hevc" and pix_fmt in ("yuv420p10le",)))

    # Prefer an English track, then an already-AAC one, then the first.
    def lang(s):
        return (s.get("tags", {}).get("language") or "").lower()
    audio = None
    if audios:
        audio = next((a for a in audios if lang(a) in ("eng", "en") and a.get("codec_name") in IOS_AUDIO), None) \
            or next((a for a in audios if lang(a) in ("eng", "en")), None) \
            or next((a for a in audios if a.get("codec_name") in IOS_AUDIO), None) \
            or audios[0]
    acodec = (audio or {}).get("codec_name", "")
    audio_ok = (audio is None) or acodec in IOS_AUDIO

    container_ok = ext in IOS_CONTAINERS
    ios_ok = container_ok and video_ok and audio_ok and video is not None

    text_sub = None
    if subs:
        text_subs = [s for s in subs if s.get("codec_name") in TEXT_SUBS]

        def sub_score(s):
            title = (s.get("tags", {}).get("title") or "").lower()
            junk = any(w in title for w in ("sign", "song", "karaoke", "op/ed", "oped", "forced")) \
                or bool(s.get("disposition", {}).get("forced"))
            return (not junk, lang(s) in ("eng", "en"), -subs.index(s))

        if text_subs:
            text_sub = max(text_subs, key=sub_score)

    return {
        "ios_ok": ios_ok,
        "has_video": video is not None,
        "vcodec": vcodec, "acodec": acodec, "pix_fmt": pix_fmt, "height": height, "duration": duration,
        "video_index": streams.index(video) if video else None,
        "audio_index": streams.index(audio) if audio else None,
        "sub_index": streams.index(text_sub) if text_sub else None,
        "copy_video": video_ok,
        "copy_audio": audio_ok,
        "hevc": vcodec == "hevc",
    }


# --------------------------------------------------------------- converting

class Job:
    def __init__(self, rel: str):
        self.rel = rel
        self.percent = 0.0
        self.stage = "starting"     # starting | remux | transcode | finishing
        self.started = time.time()


class Converter:
    """Background worker: probes durations/playability, converts what iPhone can't play."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.current: Optional[Job] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

    # -- public -------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        db.reset_stale_conversions()
        self._sweep_temp_files()
        self._thread = threading.Thread(target=self._loop, name="converter", daemon=True)
        self._thread.start()

    def _sweep_temp_files(self) -> None:
        """Remove half-written outputs left behind if the server was stopped mid-conversion."""
        for dirpath, dirnames, filenames in os.walk(self.cfg.library):
            dirnames[:] = [d for d in dirnames if d != ORIGINALS_DIR]
            for name in filenames:
                if name.startswith(".") and name.endswith(".converting.mp4"):
                    try:
                        (Path(dirpath) / name).unlink()
                    except OSError:
                        pass

    def poke(self) -> None:
        self._wake.set()

    def free_bytes(self) -> int:
        try:
            st = os.statvfs(self.cfg.library)
            return st.f_bavail * st.f_frsize
        except OSError:
            return 1 << 60

    def status(self) -> Dict:
        job = self.current
        free = self.free_bytes()
        return {
            "free_bytes": free,
            "low_disk": free < MIN_FREE_BYTES,
            "available": available(),
            "enabled": self.cfg.convert_for_iphone,
            "current": {"rel_path": job.rel, "percent": round(job.percent, 1), "stage": job.stage,
                        "started": job.started} if job else None,
            "queue": [c for c in db.conversions() if c["status"] == "queued"],
            "failed": [c for c in db.conversions() if c["status"] == "failed"],
            "done": [c for c in db.conversions() if c["status"] == "done"][:50],
            "originals_bytes": self.originals_size(),
            "keep_originals": self.cfg.keep_originals,
        }

    def originals_size(self) -> int:
        root = self.cfg.library / ORIGINALS_DIR
        if not root.exists():
            return 0
        total = 0
        for dirpath, _, files in os.walk(root):
            for f in files:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                except OSError:
                    pass
        return total

    def delete_originals(self) -> int:
        """Move library/.originals into the macOS Bin (never a hard delete).
        Restoring or truly freeing the space is then the user's call, in Finder."""
        root = self.cfg.library / ORIGINALS_DIR
        size = self.originals_size()
        if not root.exists():
            return 0
        import sys
        stamp = time.strftime("%Y-%m-%d %H.%M.%S")
        if sys.platform != "darwin":
            # no Bin API off macOS: park them next to the library, clearly labelled
            target = self.cfg.library.parent / f"Matinee originals {stamp}"
            shutil.move(str(root), str(target))
            return size
        target = Path.home() / ".Trash" / f"Matinee originals {stamp}"
        try:
            os.rename(root, target)          # instant when library is on the same disk
        except OSError:
            # library on another volume: ask Finder to do it (may prompt once for permission)
            import subprocess

            r = subprocess.run(
                ["osascript", "-e", f'tell application "Finder" to delete POSIX file "{root}"'],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0:
                raise RuntimeError("Could not move the originals to the Bin")
        return size

    # -- worker -------------------------------------------------------
    def _loop(self) -> None:
        while True:
            try:
                did = self._pass()
            except Exception:
                log.exception("converter pass failed")
                did = False
            if not did:
                self._wake.wait(timeout=20)
                self._wake.clear()

    def _pass(self) -> bool:
        if not available():
            return False
        # 1) fill in duration / playability for anything not probed yet (cheap, few per pass)
        probed = 0
        for v in db.videos_needing_probe(limit=25):
            path = self.cfg.library / v["rel_path"]
            if not path.is_file():
                continue
            info = probe(path)
            if info is None:
                db.set_probe(v["id"], None, None)
                continue
            a = analyse(info, v["ext"])
            db.set_probe(v["id"], a["duration"], a["ios_ok"])
            probed += 1
        # 2) convert one file that iPhone can't play
        if self.cfg.convert_for_iphone:
            cand = self._next_candidate()
            if cand:
                self._convert(cand)
                return True
        # 3) idle work: one show's TVmaze metadata, then one scrubber-preview sheet
        if self._idle_work():
            return True
        return probed > 0

    def _idle_work(self) -> bool:
        from . import catalog, metadata, trickplay   # lazy: avoid import cycles

        rows = db.all_videos()
        for v in rows:
            parts = v["rel_path"].split("/")
            v["subfolder"] = "/".join(parts[1:-1])
        cat = catalog.build(rows)
        for folder, info in cat.items():
            if info["series"] and not metadata.has_cache(folder):
                metadata.get(folder, allow_fetch=True)
                return True
        for folder, info in cat.items():
            if info["series"]:
                continue
            for v in info["videos"]:
                if not metadata.has_movie_cache(v["title"]):
                    metadata.movie_info(v["title"])   # warms the movie-page synopsis
                    return True
        if self.free_bytes() >= MIN_FREE_BYTES:
            newest_first = sorted(rows, key=lambda v: v.get("added_at") or 0, reverse=True)
            v = trickplay.next_missing(newest_first)
            if v is not None:
                trickplay.generate(self.cfg, v)
                return True
        return False

    def _next_candidate(self) -> Optional[dict]:
        if self.free_bytes() < MIN_FREE_BYTES:
            return None   # converting doubles files on disk — don't run the machine dry
        now = time.time()
        for v in db.videos_not_ios_ok():
            if v["rel_path"].startswith(ORIGINALS_DIR + "/"):
                continue
            rec = db.get_conversion(v["rel_path"])
            if rec and rec["status"] in ("failed", "done", "converting"):
                continue
            path = self.cfg.library / v["rel_path"]
            try:
                st = path.stat()
            except OSError:
                continue
            if now - st.st_mtime < STABLE_SECONDS or st.st_size != v["size"]:
                continue   # still copying in
            if self.cfg.auto_organize:
                from . import organize  # lazy: organize imports db, not us

                p = organize.propose(v["rel_path"])
                if p.changed and p.confident:
                    continue   # about to be renamed/moved; convert it after that
            if not rec:
                db.set_conversion(v["rel_path"], "queued")
            return v
        return None

    def _convert(self, v: dict) -> None:
        rel = v["rel_path"]
        src = self.cfg.library / rel
        job = Job(rel)
        self.current = job
        db.set_conversion(rel, "converting")
        try:
            out_rel = self._run(src, rel, job)
        except Exception as e:
            log.exception("conversion failed for %s", rel)
            db.set_conversion(rel, "failed", error=str(e)[:500])
            self.current = None
            return
        db.set_conversion(rel, "done", output=out_rel)
        self.current = None

    def _run(self, src: Path, rel: str, job: Job) -> str:
        info = probe(src)
        if not info:
            raise RuntimeError("ffprobe could not read the file")
        a = analyse(info, src.suffix.lstrip(".").lower())
        if not a["has_video"]:
            raise RuntimeError("No video stream")
        duration = a["duration"] or 0

        stem = src.name[: -len(src.suffix)] if src.suffix else src.name
        final = src.with_name(stem + ".mp4")
        tmp = src.with_name(f".{stem}.converting.mp4")
        sub_out = src.with_name(stem + ".srt")

        cmd = [ffmpeg_path(), "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-i", str(src), "-map", f"0:{a['video_index']}"]
        if a["audio_index"] is not None:
            cmd += ["-map", f"0:{a['audio_index']}"]
        if a["copy_video"]:
            cmd += ["-c:v", "copy"]
            if a["hevc"]:
                cmd += ["-tag:v", "hvc1"]
            job.stage = "remux"
        else:
            cmd += self._video_encoder_args(a["height"])
            job.stage = "transcode"
        if a["audio_index"] is not None:
            cmd += ["-c:a", "copy"] if a["copy_audio"] else ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += ["-movflags", "+faststart", "-max_muxing_queue_size", "2048",
                "-progress", "pipe:1", "-nostats", str(tmp)]

        try:
            self._exec(cmd, job, duration)
        except RuntimeError as e:
            if a["copy_video"] or "videotoolbox" not in " ".join(cmd):
                raise
            # hardware encoder refused this file: fall back to software x264
            log.warning("videotoolbox failed for %s (%s); retrying with libx264", rel, e)
            idx = cmd.index("h264_videotoolbox")
            cmd = cmd[: idx - 1] + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"] + cmd[idx + 1:]
            cmd = [c for i, c in enumerate(cmd) if not (c in ("-b:v",) or (i > 0 and cmd[i - 1] == "-b:v"))]
            self._exec(cmd, job, duration)

        job.stage = "finishing"
        check = probe(tmp)
        out_dur = float((check or {}).get("format", {}).get("duration") or 0)
        if not check or (duration and abs(out_dur - duration) > max(2.0, duration * 0.02)):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Output looks wrong (duration {out_dur:.0f}s vs {duration:.0f}s)")

        # embedded text subtitles -> sidecar .srt so the player can offer them
        if a["sub_index"] is not None:   # always refresh — the sidecar is our own artifact
            try:
                subprocess.run([ffmpeg_path(), "-y", "-nostdin", "-loglevel", "error", "-i", str(src),
                                "-map", f"0:{a['sub_index']}", "-c:s", "srt", str(sub_out)],
                               capture_output=True, timeout=600)
                if sub_out.exists() and sub_out.stat().st_size == 0:
                    sub_out.unlink()
            except (OSError, subprocess.TimeoutExpired):
                pass

        # swap: original -> .originals/<rel> (or delete), tmp -> final name
        if self.cfg.keep_originals:
            parked = self.cfg.library / ORIGINALS_DIR / rel
            parked.parent.mkdir(parents=True, exist_ok=True)
            if parked.exists():
                parked.unlink()
            shutil.move(str(src), str(parked))
        else:
            src.unlink()
        os.replace(str(tmp), str(final))
        out_rel = final.relative_to(self.cfg.library).as_posix()
        if out_rel != rel:
            db.move_video(rel, out_rel)
            db.log_move(rel, out_rel, auto=True, kind="converted")
        vid = db.get_video_by_path(out_rel)
        if vid:
            db.set_probe(vid["id"], duration or None, True)
            st_out = final.stat()
            db.update_file_stats(out_rel, st_out.st_size, st_out.st_mtime)
        log.info("converted %s -> %s (%s)", rel, out_rel, job.stage)
        return out_rel

    def _video_encoder_args(self, height: int) -> List[str]:
        bitrate = "2500k" if height <= 720 else "5000k" if height <= 1080 else "12000k"
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-profile:v", "high", "-pix_fmt", "yuv420p"]

    def retry(self, rel_path: str) -> None:
        db.clear_conversion(rel_path)
        self.poke()

    def _exec(self, cmd: List[str], job: Job, duration: float) -> None:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                preexec_fn=lambda: os.nice(10))
        self._proc = proc
        err_lines: List[str] = []

        def drain_err():
            for line in proc.stderr:
                err_lines.append(line.rstrip())

        t = threading.Thread(target=drain_err, daemon=True)
        t.start()
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    if duration:
                        job.percent = max(0.0, min(99.0, us / 1_000_000 / duration * 100))
                except ValueError:
                    pass
        proc.wait()
        t.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
        self._proc = None
        if proc.returncode != 0:
            raise RuntimeError(" ".join(err_lines[-3:]) or f"ffmpeg exited with {proc.returncode}")
        job.percent = 100.0


_instance: Optional[Converter] = None


def get_converter(cfg: Config) -> Converter:
    global _instance
    if _instance is None:
        _instance = Converter(cfg)
    return _instance
