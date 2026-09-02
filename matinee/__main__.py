"""`python -m matinee` — serve (the app prints its URLs once it has scanned the library)."""
from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="matinee")
    parser.add_argument("--port", type=int, help="override port from config.yaml")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    port = args.port or cfg.port
    if args.port:
        os.environ["MATINEE_PORT"] = str(args.port)  # so the app's banner shows the right URLs

    uvicorn.run(
        "matinee.app:app",
        host="0.0.0.0",
        port=port,
        reload=args.reload,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
