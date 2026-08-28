from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp",
}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
SCRIPT_EXTS = {".funscript"}

_FUZZY_SUFFIX_RE = re.compile(r"[\s._-]*\(?\b(19|20)\d{2}\b\)?\s*$")


@dataclass
class PlaylistItem:
    media_path: str | None   # None = script-only item, no accompanying media
    script_path: str | None

    @property
    def name(self) -> str:
        if self.media_path:
            return Path(self.media_path).name
        if self.script_path:
            return f"{Path(self.script_path).name} (script only)"
        return "Unknown"

    @property
    def has_script(self) -> bool:
        return bool(self.script_path)

    @property
    def is_script_only(self) -> bool:
        return self.media_path is None


def _fuzzy_key(stem: str) -> str:
    return _FUZZY_SUFFIX_RE.sub("", stem).strip().lower()


def pair_folder(folder: str | Path, fuzzy: bool = True) -> list[PlaylistItem]:
    """Scan a folder for media files and pair each with a same-named .funscript."""
    folder = Path(folder)
    media_files = [p for p in folder.rglob("*") if p.suffix.lower() in MEDIA_EXTS]
    script_files = [p for p in folder.rglob("*") if p.suffix.lower() in SCRIPT_EXTS]

    by_stem: dict[str, Path] = {}
    by_fuzzy: dict[str, Path] = {}
    for sp in script_files:
        stem = sp.name
        for ext in (".funscript",):
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
        by_stem[stem.lower()] = sp
        if fuzzy:
            by_fuzzy.setdefault(_fuzzy_key(stem), sp)

    items = []
    for mp in sorted(media_files):
        stem = mp.stem
        script = by_stem.get(stem.lower())
        if script is None and fuzzy:
            script = by_fuzzy.get(_fuzzy_key(stem))
        items.append(PlaylistItem(str(mp), str(script) if script else None))
    return items


class Playlist:
    def __init__(self) -> None:
        self.items: list[PlaylistItem] = []
        self._order: list[int] = []
        self._pos: int = -1
        self.shuffle: bool = False
        self.repeat_mode: str = "off"  # off | one | all

    def set_items(self, items: list[PlaylistItem]) -> None:
        self.items = items
        self._rebuild_order(keep_current=None)

    def add_items(self, items: list[PlaylistItem]) -> None:
        self.items.extend(items)
        self._rebuild_order(keep_current=self.current_item())

    def clear(self) -> None:
        self.items = []
        self._order = []
        self._pos = -1

    def remove_at(self, index: int) -> None:
        current = self.current_item()
        del self.items[index]
        self._rebuild_order(keep_current=current)

    def set_shuffle(self, enabled: bool) -> None:
        self.shuffle = enabled
        self._rebuild_order(keep_current=self.current_item())

    def _rebuild_order(self, keep_current: PlaylistItem | None) -> None:
        self._order = list(range(len(self.items)))
        if self.shuffle:
            random.shuffle(self._order)
        if keep_current is not None and keep_current in self.items:
            idx = self.items.index(keep_current)
            pos_in_order = self._order.index(idx)
            self._order[0], self._order[pos_in_order] = self._order[pos_in_order], self._order[0]
            self._pos = 0
        else:
            self._pos = -1

    def current_item(self) -> PlaylistItem | None:
        if 0 <= self._pos < len(self._order):
            return self.items[self._order[self._pos]]
        return None

    def select_index(self, index: int) -> PlaylistItem | None:
        if index not in self._order:
            return None
        self._pos = self._order.index(index)
        return self.current_item()

    def next(self) -> PlaylistItem | None:
        if not self._order:
            return None
        if self.repeat_mode == "one":
            return self.current_item()
        if self._pos + 1 < len(self._order):
            self._pos += 1
            return self.current_item()
        if self.repeat_mode == "all":
            self._pos = 0
            if self.shuffle:
                random.shuffle(self._order)
            return self.current_item()
        return None

    def previous(self) -> PlaylistItem | None:
        if not self._order:
            return None
        if self._pos - 1 >= 0:
            self._pos -= 1
            return self.current_item()
        if self.repeat_mode == "all":
            self._pos = len(self._order) - 1
            return self.current_item()
        return None

    # ---- persistence ----

    def save(self, path: str | Path) -> None:
        data = {
            "items": [{"media": i.media_path, "script": i.script_path} for i in self.items],
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "Playlist":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pl = cls()
        pl.shuffle = data.get("shuffle", False)
        pl.repeat_mode = data.get("repeat_mode", "off")
        items = [PlaylistItem(i.get("media"), i.get("script")) for i in data.get("items", [])]
        pl.set_items(items)
        return pl
