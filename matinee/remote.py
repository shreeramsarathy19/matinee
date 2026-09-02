"""Phone-as-remote: the laptop's browser registers as a "screen" and polls for
commands; the phone lists screens and sends play/pause/seek/next/stop.

All state is in memory — a restart simply drops screens until they re-report
(every couple of seconds), so nothing needs persisting.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

ONLINE_SECONDS = 12   # a screen is "online" if it reported within this window
FORGET_SECONDS = 120  # drop screens not seen for this long


class Remote:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._screens: Dict[str, dict] = {}
        self._queues: Dict[str, List[dict]] = {}

    def report(self, screen_id: str, name: Optional[str], state: Optional[dict]) -> List[dict]:
        """A screen reports what it's doing; returns any commands queued for it."""
        with self._lock:
            self._screens[screen_id] = {
                "id": screen_id,
                "name": (name or "Screen")[:40],
                "last_seen": time.time(),
                "state": state,
            }
            self._prune()
            return self._queues.pop(screen_id, [])

    def send(self, screen_id: str, command: dict) -> bool:
        with self._lock:
            s = self._screens.get(screen_id)
            if not s or time.time() - s["last_seen"] > ONLINE_SECONDS:
                return False
            self._queues.setdefault(screen_id, []).append(command)
            return True

    def screens(self) -> List[dict]:
        now = time.time()
        with self._lock:
            self._prune()
            return [
                {"id": s["id"], "name": s["name"], "state": s["state"],
                 "online": now - s["last_seen"] <= ONLINE_SECONDS}
                for s in self._screens.values()
            ]

    def _prune(self) -> None:
        cutoff = time.time() - FORGET_SECONDS
        for sid in [k for k, s in self._screens.items() if s["last_seen"] < cutoff]:
            self._screens.pop(sid, None)
            self._queues.pop(sid, None)
