"""Background duration lookup for playlist entries.

Media files are probed with ffprobe (must be on PATH; the flake wraps it in).
.funscript files are read directly -- their length is the last action time.
Results are cached on disk keyed by path + mtime + size, so each file is
probed once and again only if it changes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .config import config_dir


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def probe_duration(path: str) -> float | None:
    """Length in seconds, or None if it can't be determined."""
    try:
        if path.lower().endswith(".funscript"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ats = [int(a["at"]) for a in data.get("actions", [])]
            return (max(ats) / 1000.0) if ats else 0.0
        exe = shutil.which("ffprobe")
        if not exe:
            return None
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", os.path.abspath(path)],
            capture_output=True, text=True, timeout=20,
        )
        text = out.stdout.strip()
        return float(text) if text and text != "N/A" else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _stamp(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


class DurationService(QObject):
    """Cached, threaded duration lookups. All public methods: main thread only."""

    updated = Signal(str, float)   # path, seconds -- emitted per newly known duration
    idle = Signal()                # all queued probes finished

    _done = Signal(str, object, object)  # internal: worker thread -> main thread

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cache: dict[str, tuple[int, int, float]] = {}
        self._inflight: set[str] = set()
        self._failed: set[str] = set()   # session-only; retried on next launch
        self._dirty = False
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="probe")
        self._done.connect(self._on_done)
        self._load()

    # ---- public ----

    def get(self, path: str | None) -> float | None:
        if not path:
            return None
        entry = self._cache.get(path)
        return entry[2] if entry else None

    def request(self, paths) -> None:
        """Queue probes for any path that isn't cached (or changed on disk)."""
        for path in paths:
            if not path or self._closed or path in self._inflight or path in self._failed:
                continue
            stamp = _stamp(path)
            if stamp is None:
                continue
            entry = self._cache.get(path)
            if entry and (entry[0], entry[1]) == stamp:
                continue
            self._inflight.add(path)
            self._pool.submit(self._work, path, stamp)

    def shutdown(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._save()

    # ---- internals ----

    def _work(self, path: str, stamp: tuple[int, int]) -> None:
        duration = probe_duration(path)
        if self._closed:
            return
        try:
            self._done.emit(path, stamp, duration)
        except RuntimeError:
            pass  # service deleted during shutdown

    def _on_done(self, path: str, stamp, duration) -> None:
        self._inflight.discard(path)
        if duration is None:
            self._failed.add(path)
        else:
            self._cache[path] = (stamp[0], stamp[1], float(duration))
            self._dirty = True
            self.updated.emit(path, float(duration))
        if not self._inflight:
            self._save()
            self.idle.emit()

    @staticmethod
    def _cache_file() -> Path:
        return config_dir() / "durations.json"

    def _load(self) -> None:
        try:
            with open(self._cache_file(), "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._cache = {k: (int(v[0]), int(v[1]), float(v[2])) for k, v in raw.items()}
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            self._cache = {}

    def _save(self) -> None:
        if not self._dirty:
            return
        tmp = self._cache_file().with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({k: list(v) for k, v in self._cache.items()}, f)
            os.replace(tmp, self._cache_file())
            self._dirty = False
        except OSError:
            pass
