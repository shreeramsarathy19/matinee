"""Load config.yaml and resolve paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "matinee.db"

DEFAULT_EXTENSIONS = ["mp4", "m4v", "mov", "webm", "mkv", "avi"]


@dataclass
class Config:
    library: Path = ROOT / "library"
    port: int = 8765
    extensions: List[str] = field(default_factory=lambda: list(DEFAULT_EXTENSIONS))
    rescan_seconds: int = 30
    auto_organize: bool = True
    convert_for_iphone: bool = True
    keep_originals: bool = True
    profiles: List[str] = field(default_factory=lambda: ["You"])
    app_name: str = "Matinee"
    skip_seconds: int = 30
    opensubtitles: dict = field(default_factory=dict)


def load_config() -> Config:
    cfg = Config()
    _custom_library = False
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if raw.get("library"):
            _custom_library = True
            lib = Path(str(raw["library"])).expanduser()
            cfg.library = lib if lib.is_absolute() else (ROOT / lib).resolve()
        if raw.get("port"):
            cfg.port = int(raw["port"])
        if raw.get("extensions"):
            cfg.extensions = [str(e).lower().lstrip(".") for e in raw["extensions"]]
        if raw.get("rescan_seconds"):
            cfg.rescan_seconds = max(5, int(raw["rescan_seconds"]))
        if "auto_organize" in raw:
            cfg.auto_organize = bool(raw["auto_organize"])
        if "convert_for_iphone" in raw:
            cfg.convert_for_iphone = bool(raw["convert_for_iphone"])
        if "keep_originals" in raw:
            cfg.keep_originals = bool(raw["keep_originals"])
        if raw.get("app_name"):
            cfg.app_name = str(raw["app_name"]).strip()[:24] or "Matinee"
        if raw.get("skip_seconds"):
            cfg.skip_seconds = max(5, min(120, int(raw["skip_seconds"])))
        if isinstance(raw.get("opensubtitles"), dict):
            cfg.opensubtitles = raw["opensubtitles"]
        if raw.get("profiles"):
            names = [str(n).strip() for n in raw["profiles"] if str(n).strip()]
            if names:
                cfg.profiles = list(dict.fromkeys(names))  # dedupe, keep order
    if os.environ.get("MATINEE_PORT"):
        cfg.port = int(os.environ["MATINEE_PORT"])
    if not cfg.library.exists() and not _custom_library:
        # only ever create the DEFAULT ./library (fresh install). A configured
        # path that is missing means an unplugged disk or a moved folder — creating
        # an empty stand-in would silently hide that, so leave it missing.
        cfg.library.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return cfg
