"""Serve sidecar subtitles as WebVTT (the only format <track> understands)."""
from __future__ import annotations

import re
from pathlib import Path

_TIMESTAMP = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


VTT_TIME = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})")


def shift(vtt: str, offset: float) -> str:
    """Move every cue by `offset` seconds (clamped at 0) — the player's sync nudge."""
    if not offset:
        return vtt
    def one(m):
        t = (int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
             + int(m.group(4)) / 1000 + offset)
        t = max(0.0, t)
        ms = round((t % 1) * 1000)
        return f"{int(t // 3600):02}:{int(t % 3600 // 60):02}:{int(t % 60):02}.{ms:03}"
    return "\n".join(VTT_TIME.sub(one, l) if "-->" in l else l for l in vtt.split("\n"))


def to_vtt(path: Path) -> str:
    text = _read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    if path.suffix.lower() == ".vtt" or text.lstrip().startswith("WEBVTT"):
        return text if text.lstrip().startswith("WEBVTT") else "WEBVTT\n\n" + text

    # ASS/SSA remnants: {\an5}, {\k12}, ... and <font> wrappers show as garbage in players
    text = re.sub(r"\{\\[^}]*\}", "", text)
    text = re.sub(r"</?font[^>]*>", "", text, flags=re.I)
    # styling wrappers some subtitle tools emit (<Default>, <span>...) — VTT only knows b/i/u
    text = re.sub(r"</?(?!/?[biu]>)[A-Za-z][A-Za-z0-9_]*[^>]*>", "", text)

    out = ["WEBVTT", ""]
    for block in re.split(r"\n{2,}", text.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        if lines[0].strip().isdigit():  # drop the SRT cue number
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        timing = _TIMESTAMP.sub(lambda m: f"{int(m[1]):02}:{m[2]}:{m[3]}.{m[4].ljust(3, '0')}", lines[0])
        out.append(timing)
        out.extend(lines[1:])
        out.append("")
    return "\n".join(out)
