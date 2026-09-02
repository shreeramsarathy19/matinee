"""Turn messy download names into a tidy library.

    Breaking.Bad.S01E03.720p.HDTV.x264-KILLERS.mkv  ->  Breaking Bad/Season 01/Breaking Bad - S01E03.mkv
    The.Matrix.1999.1080p.BluRay.x265-RARBG.mp4      ->  Movies/The Matrix (1999).mp4

`plan()` proposes moves, `apply()` performs them (keeping watch progress), and the
rescan loop calls `auto_organize()` so anything dropped into the library gets tidied
on its own once the file has finished copying.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import db
from .config import Config

log = logging.getLogger("matinee.organize")

MOVIES_FOLDER = "Movies"
SUB_EXTS = {"srt", "vtt", "sub", "idx", "ass", "ssa"}
# Left behind in release folders; safe to sweep when the videos have moved out.
JUNK_EXTS = {"nfo", "txt", "sfv", "md5", "url", "jpg", "jpeg", "png", "gif", "exe", "torrent", "website", "htm", "html"}
STABLE_SECONDS = 60  # a file must be untouched this long before we auto-move it (copy finished)

# Release metadata: the title ends where the first of these starts.
STRONG = re.compile(
    r"""(?<![a-z0-9])(?:
        \d{3,4}p|\d{3,4}x\d{3,4}|4k|uhd|
        blu-?ray|bd-?rip|br-?rip|bd-?remux|remux|web-?rip|web-?dl|hdtv|pdtv|dvd-?rip|dvdscr|hdrip|hd-?cam|camrip|telesync|
        x\.?26[45]|h\.?26[45]|hevc|avc|xvid|divx|10-?bit|8-?bit|hdr10\+?|hdr|
        aac(?:2\.0)?|ac-?3|dd[p+]?(?:5\.1|2\.0|7\.1)?|dts(?:-?hd)?|true-?hd|atmos|eac3|flac|
        amzn|dsnp|hmax|atvp|hulu|pcok|
        yify|yts(?:\.\w+)?|rarbg|eztv|ettv|galaxytv|megusta|mkvcage|shaanig|xebec|sparks|killers|dimension|fleet|ion10
    )(?![a-z0-9])""",
    re.I | re.X,
)
# Removed from what's left over (episode titles), but never used to cut a title short.
WEAK = re.compile(
    r"(?<![a-z0-9])(?:proper|repack|rerip|extended(?:\s+cut)?|remastered|internal|limited|unrated|uncut|dubbed|subbed|"
    r"multi|dual[\s-]?audio|esubs?|msubs?|hindi|english|korean|japanese|complete|season\s*pack)(?![a-z0-9])",
    re.I,
)
YEAR = re.compile(r"(?<!\d)(?<!\dx)(19[2-9]\d|20[0-4]\d)(?!\d|x\d)")

# Episode markers, most specific first. Groups: show text before, season, episode, [episode end], text after.
EPISODE_PATTERNS = [
    # S01E03, S01 E03, S1.E3, S01E03E04, S01E03-E04, S01E03-04
    re.compile(r"(?P<pre>.*?)(?<![a-z0-9])S(?P<s>\d{1,2})[ ._-]*E(?P<e>\d{1,3})(?:(?:[ ._-]*E|-)(?P<e2>\d{1,3}))?(?![0-9])(?P<post>.*)", re.I),
    # Season 1 Episode 3 / Season 1 Ep 3 / Series 1 Episode 3
    re.compile(r"(?P<pre>.*?)(?<![a-z0-9])(?:Season|Series)[ ._-]*(?P<s>\d{1,2})[ ._-]*(?:Episode|Ep|E)[ ._-]*(?P<e>\d{1,3})(?![0-9])(?P<post>.*)", re.I),
    # 1x03 (not 1920x1080)
    re.compile(r"(?P<pre>.*?)(?<![a-z0-9])(?P<s>\d{1,2})x(?P<e>\d{2,3})(?![0-9])(?P<post>.*)", re.I),
    # Episode 3 / Ep 3 / Ep.03 / E03 (season comes from the folder, else 1)
    re.compile(r"(?P<pre>.*?)(?<![a-z0-9])(?:Episode|Ep\.?|E)[ ._-]*(?P<e>\d{1,3})(?![0-9])(?P<post>.*)", re.I),
    # Anime style: "Show Name - 03 [1080p]" / "Show Name - 03"
    re.compile(r"(?P<pre>.+?)\s+-\s+(?P<e>\d{1,3})(?![0-9])(?P<post>(?:\s|\[|\(|$).*)", re.I),
]
SEASON_ONLY = re.compile(r"(?<![a-z0-9])(?:S|Season|Series)[ ._-]*(?P<s>\d{1,2})(?![a-z0-9])", re.I)
SEASON_FOLDER = re.compile(r"^(?:Season|Series|S)[ ._-]*(?P<s>\d{1,2})$", re.I)
SMALL_WORDS = {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for", "by", "vs", "with", "from"}


@dataclass
class Plan:
    src: str               # current path relative to the library
    dst: str               # proposed path relative to the library
    kind: str              # "episode" | "movie" | "unknown"
    show: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_end: Optional[int] = None
    title: str = ""        # movie title / episode title
    year: Optional[int] = None
    confident: bool = False   # safe to apply without a human looking
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.src != self.dst

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["changed"] = self.changed
        return d


# ------------------------------------------------------------------ cleaning

# Episode markers that may hide inside [brackets]: [S1-E02], [S01 E010], [1x05]
BRACKET_EP = re.compile(
    r"(?i)(?<![a-z0-9])(?:S\d{1,2}[ ._-]*E\d{1,4}|\d{1,2}x\d{2,3}|Season\s*\d+|Episode\s*\d+|E\d{2,3})(?![a-z0-9])"
)
GROUP_PREFIX = re.compile(r"^\s*\[[^\]]{1,24}\]\s*")   # "[SubTeam] Title ..." leading tag


def _normalize(stem: str) -> str:
    """Scene names use dots/underscores for spaces; keep real dots when spaces exist ("Mr. Robot")."""
    s = stem.replace("_", " ")
    m = GROUP_PREFIX.match(s)
    if m and not BRACKET_EP.search(m.group(0)):
        s = s[m.end():]                       # drop a leading release-group tag
    if " " not in s.strip():
        s = s.replace(".", " ")

    def debracket(mm):
        inner = mm.group(1) or ""
        # keep bracket content that carries the episode number; junk tags vanish
        return f" {inner} " if inner and BRACKET_EP.search(inner) else " "

    s = re.sub(r"\[([^\]]*)\]|\{[^}]*\}", debracket, s)   # [rarbg] [1080p] {x264}
    s = re.sub(r"\((?=[^)]*(?:\d{3,4}p|x26[45]|hevc|web|blu))[^)]*\)", " ", s, flags=re.I)  # (1080p WEB-DL)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_tail(s: str) -> str:
    """Drop release words and dangling separators from a title fragment."""
    s = STRONG.split(s)[0]
    s = WEAK.sub(" ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^[\s\-–—_.:]+|[\s\-–—_.:(\[]+$", "", s)
    return s.strip()


def _nice_case(s: str) -> str:
    if not s or (s != s.lower() and s != s.upper()):
        return s                     # mixed case: the name already has deliberate casing
    if s == s.upper() and len(s) <= 4 and " " not in s:
        return s                     # JFK, MASH: probably an acronym
    words = s.split(" ")
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if (lw in SMALL_WORDS and i not in (0, len(words) - 1)) else (lw[:1].upper() + lw[1:]))
    return " ".join(out)


def _safe(s: str) -> str:
    """Make a path component safe for macOS/exFAT/Windows."""
    s = s.replace("/", "-").replace(":", " -").replace("\\", "-")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s


def _title_and_year(fragment: str) -> Tuple[str, Optional[int]]:
    """'The Matrix 1999 1080p' -> ('The Matrix', 1999). A year at position 0 is a title ('1917')."""
    fragment = STRONG.split(fragment)[0]
    year = None
    matches = [m for m in YEAR.finditer(fragment) if m.start() > 0]
    if matches:
        m = matches[-1]
        year = int(m.group(1))
        fragment = fragment[: m.start()]
    title = _nice_case(_strip_tail(fragment))
    # a year wrapped in brackets: "Name (1999)" -> strip the empty "()"
    title = re.sub(r"\(\s*\)$", "", title).strip()
    return title, year


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _same_title(a: str, b: str) -> bool:
    """Is `a` effectively the show name `b`? (exact, contained, or nearly identical)"""
    ka, kb = _norm_key(a), _norm_key(b)
    if not ka or not kb:
        return not ka
    if ka == kb or ka in kb or kb in ka:
        return True
    import difflib

    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.75


def _folder_show(name: str) -> Tuple[str, Optional[int]]:
    """'Breaking.Bad.S01.1080p.WEB-DL' -> ('Breaking Bad', 1); 'Breaking Bad' -> ('Breaking Bad', None)."""
    s = _normalize(name)
    season = None
    m = SEASON_ONLY.search(s)
    if m and not re.search(r"(?<![a-z0-9])S\d{1,2}[ ._-]*E\d", s, re.I):
        season = int(m.group("s"))
        s = s[: m.start()] + " " + s[m.end():]
    title, year = _title_and_year(s)
    return (f"{title} ({year})" if year and title else title), season


def _is_messy(folder: str) -> bool:
    """Does this folder name look like a download/release name rather than one the user typed?"""
    if SEASON_FOLDER.match(folder.strip()):
        return False                 # "Season 1" is structure, not a release name
    s = _normalize(folder)
    return bool(STRONG.search(s) or SEASON_ONLY.search(s) or ("." in folder and " " not in folder and len(folder) > 12))


# ------------------------------------------------------------------- parsing

def parse_name(stem: str) -> Dict:
    """Parse a file name (without extension) into show/season/episode or title/year."""
    s = _normalize(stem)
    # Looks downloaded: release words, dotted.scene.naming, or [bracketed] tags
    scene_like = bool(STRONG.search(stem.replace(".", " "))) or ("." in stem and " " not in stem) \
        or bool(re.search(r"\[[^\]]{2,}\]", stem))

    for pat in EPISODE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        gd = m.groupdict()
        pre, post = gd.get("pre", ""), gd.get("post", "")
        season = int(gd["s"]) if gd.get("s") else None
        episode = int(gd["e"])
        episode_end = int(gd["e2"]) if gd.get("e2") else None
        if episode_end is not None and episode_end <= episode:
            episode_end = None
        show, year = _title_and_year(pre)
        if pat is EPISODE_PATTERNS[-1] and (len(show) < 2 or YEAR.fullmatch(show or "")):
            continue  # "Something - 2" with no real name: don't treat as an episode
        if pat is EPISODE_PATTERNS[3] and show and len(show) < 2:
            continue
        raw_title = _strip_tail(post)
        # "S01E01-KILLERS": a lone ALL-CAPS token glued on with a dash is the release group, not a title
        if re.match(r"\s*-[A-Za-z0-9]", post) and re.fullmatch(r"[A-Z0-9]{2,12}", raw_title or ""):
            raw_title = ""
        title = _nice_case(raw_title)
        return {
            "kind": "episode", "show": show, "year": year, "season": season, "episode": episode,
            "episode_end": episode_end, "title": title, "scene_like": scene_like,
        }

    title, year = _title_and_year(s)
    return {"kind": "movie", "title": title, "year": year, "scene_like": scene_like}


def propose(rel_path: str) -> Plan:
    """Work out where a library file should live. rel_path uses '/' separators."""
    parts = rel_path.split("/")
    filename = parts[-1]
    chain = parts[:-1]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    stem = filename[: -(len(ext) + 1)] if ext else filename

    info = parse_name(stem)

    # Split the folder chain into what the user built (base) and incoming release folders (messy).
    base: List[str] = []
    messy: List[str] = []
    for f in chain:
        if messy or _is_messy(f):
            messy.append(f)
        else:
            base.append(f)

    if info["kind"] == "episode":
        show, season = info["show"], info["season"]
        year = info["year"]
        # Folder names supply the show/season when the file name doesn't.
        folder_show, folder_season = "", None
        for f in reversed(messy):
            fs, fseason = _folder_show(f)
            folder_season = folder_season or fseason
            if fs and not folder_show and not SEASON_FOLDER.match(f):
                folder_show = fs
        show_dir = list(base)
        if base and SEASON_FOLDER.match(base[-1]):
            folder_season = folder_season or int(SEASON_FOLDER.match(base[-1]).group("s"))
            show_dir = base[:-1]
            if show_dir:
                show = show_dir[-1]          # "Breaking Bad/Season 1/ep.mkv": trust the user's folder
        elif base and show and _norm_key(base[-1]) in (_norm_key(show), _norm_key(f"{show} {year or ''}")):
            show = base[-1]                  # "Shogun 2024/shogun.2024.s01e02" keeps the user's folder name
        elif base and show and _norm_key(base[-1]).startswith(_norm_key(show)) and year is None:
            show = base[-1]                  # "Doctor Who (2005)/doctor.who.s01e01" keeps the year folder
        elif base and not show:
            show = base[-1] if not folder_show else folder_show
            if not folder_show:
                show_dir = list(base)
        if not show:
            show = folder_show
        if not show:
            return Plan(src=rel_path, dst=rel_path, kind="unknown", note="Couldn't work out the show name")
        if year and str(year) not in show:
            show = f"{show} ({year})"
        show = _safe(show)
        if not show_dir or _norm_key(show_dir[-1]) != _norm_key(show):
            show_dir = show_dir + [show]
        if season is None:
            season = folder_season or 1
        ep = f"S{season:02d}E{info['episode']:02d}"
        if info["episode_end"]:
            ep += f"-E{info['episode_end']:02d}"
        name = f"{show} - {ep}"
        if info["title"] and not _same_title(info["title"], show):
            name += f" - {_safe(info['title'])}"
        dst = "/".join(show_dir + [f"Season {season:02d}", f"{name}.{ext}"])
        return Plan(src=rel_path, dst=dst, kind="episode", show=show, season=season, episode=info["episode"],
                    episode_end=info["episode_end"], title=info["title"], year=year, confident=True)

    # Movie (or anything that isn't an episode)
    title, year = info["title"], info["year"]
    if not title and messy:
        title, year = _title_and_year(_normalize(messy[0]))   # "Movie.Name.2019.1080p/movie.mkv"
    if not title:
        return Plan(src=rel_path, dst=rel_path, kind="unknown", note="Couldn't work out a title")
    name = _safe(f"{title} ({year})" if year else title)
    folder = base if base else [MOVIES_FOLDER]
    dst = "/".join(folder + [f"{name}.{ext}"])
    confident = bool(info["scene_like"] or messy)      # looks downloaded; otherwise leave user names alone
    return Plan(src=rel_path, dst=dst, kind="movie", title=title, year=year, confident=confident)


# ---------------------------------------------------------------- planning

def _all_files(cfg: Config) -> List[Tuple[str, os.stat_result]]:
    exts = set(cfg.extensions)
    out = []
    for dirpath, dirnames, filenames in os.walk(cfg.library):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in exts:
                full = Path(dirpath) / name
                try:
                    out.append((full.relative_to(cfg.library).as_posix(), full.stat()))
                except OSError:
                    pass
    return out


def _is_sample(rel: str, size: int) -> bool:
    low = rel.lower()
    return ("sample" in Path(low).stem or "/sample" in low) and size < 300 * 1024 * 1024


def plan(cfg: Config) -> List[Plan]:
    plans = []
    taken = set()
    undone = db.undone_pairs()
    for rel, st in sorted(_all_files(cfg)):
        if _is_sample(rel, st.st_size):
            continue
        p = propose(rel)
        if p.changed:
            dst_key = p.dst.lower()
            exists = (cfg.library / p.dst).exists() and p.dst.lower() != rel.lower()
            if exists or dst_key in taken:
                p.confident = False
                p.note = "A file with that name already exists"
            elif (p.src, p.dst) in undone:
                p.confident = False
                p.note = "You undid this move before — tick it to apply again"
            taken.add(dst_key)
        plans.append(p)
    return plans


# ----------------------------------------------------------------- applying

def _validate_dst(cfg: Config, src: str, dst: str) -> Optional[str]:
    if not dst or dst.startswith("/") or ".." in dst.split("/") or dst.endswith("/"):
        return "Invalid destination"
    if any(not part.strip() for part in dst.split("/")):
        return "Invalid destination"
    if src.rsplit(".", 1)[-1].lower() != dst.rsplit(".", 1)[-1].lower():
        return "Destination must keep the file extension"
    lib = cfg.library.resolve()
    try:
        (lib / dst).resolve().relative_to(lib)
    except ValueError:
        return "Destination must stay inside the library"
    return None


def _sidecars(path: Path) -> List[Path]:
    stem = path.name[: -(len(path.suffix))] if path.suffix else path.name
    out = []
    for f in path.parent.iterdir():
        if f == path or not f.is_file() or not f.name.startswith(stem):
            continue
        rest = f.name[len(stem):]
        if rest.rsplit(".", 1)[-1].lower() in SUB_EXTS:
            out.append(f)
    return out


def _sweep_empty_dirs(cfg: Config, start: Path) -> None:
    """After moving videos out, remove the now-empty release folders (junk like .nfo included)."""
    lib = cfg.library.resolve()
    d = start
    while d.resolve() != lib and lib in d.resolve().parents:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        junk = [e for e in entries if e.is_file() and (e.name == ".DS_Store" or e.suffix.lstrip(".").lower() in JUNK_EXTS)]
        if len(junk) != len(entries):
            return
        for e in junk:
            try:
                e.unlink()
            except OSError:
                return
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def move_one(cfg: Config, src: str, dst: str, auto: bool = False) -> Dict:
    err = _validate_dst(cfg, src, dst)
    if err:
        return {"src": src, "dst": dst, "ok": False, "error": err}
    src_path = cfg.library / src
    dst_path = cfg.library / dst
    if not src_path.is_file():
        return {"src": src, "dst": dst, "ok": False, "error": "Source file is gone"}
    if src == dst:
        return {"src": src, "dst": dst, "ok": True, "error": None}
    if dst_path.exists() and src.lower() != dst.lower():
        return {"src": src, "dst": dst, "ok": False, "error": "A file with that name already exists"}
    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        sidecars = _sidecars(src_path)
        shutil.move(str(src_path), str(dst_path))
        src_stem = src_path.name[: -len(src_path.suffix)] if src_path.suffix else src_path.name
        dst_stem = dst_path.name[: -len(dst_path.suffix)] if dst_path.suffix else dst_path.name
        for sc in sidecars:
            target = dst_path.parent / (dst_stem + sc.name[len(src_stem):])
            if not target.exists():
                shutil.move(str(sc), str(target))
    except OSError as e:
        return {"src": src, "dst": dst, "ok": False, "error": str(e)}
    db.move_video(src, dst)
    db.log_move(src, dst, auto)
    _sweep_empty_dirs(cfg, src_path.parent)
    log.info("%s %s -> %s", "auto-moved" if auto else "moved", src, dst)
    return {"src": src, "dst": dst, "ok": True, "error": None}


def apply(cfg: Config, moves: List[Dict], auto: bool = False) -> List[Dict]:
    return [move_one(cfg, m["src"], m["dst"], auto=auto) for m in moves]


def undo(cfg: Config, move_id: int) -> Dict:
    rec = db.get_move(move_id)
    if not rec:
        return {"ok": False, "error": "Unknown move"}
    if rec["undone"]:
        return {"ok": False, "error": "Already undone"}
    res = move_one(cfg, rec["dst"], rec["src"])
    if res["ok"]:
        db.mark_undone(move_id)
    return res


def auto_organize(cfg: Config, now: Optional[float] = None) -> List[Dict]:
    """Apply every confident plan for files that have finished copying. Called by the rescan loop."""
    now = now or time.time()
    results = []
    from . import convert  # lazy: avoid import cycle

    busy = convert.get_converter(cfg).current
    for p in plan(cfg):
        if not p.changed or not p.confident:
            continue
        if busy and busy.rel == p.src:
            continue   # ffmpeg is reading it right now
        try:
            st = (cfg.library / p.src).stat()
        except OSError:
            continue
        if now - st.st_mtime < STABLE_SECONDS:
            continue   # still being copied in; next pass
        prev = db.get_video_by_path(p.src)
        if prev and prev["size"] != st.st_size:
            continue   # grew since the last scan
        results.append(move_one(cfg, p.src, p.dst, auto=True))
    return results
