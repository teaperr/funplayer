from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VIDEO_EXTS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp",
}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
SCRIPT_EXTS = {".funscript"}

_FUZZY_SUFFIX_RE = re.compile(r"[\s._-]*\(?\b(19|20)\d{2}\b\)?\s*$")

SORT_KEYS = ("manual", "modified", "name", "length")


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
    def primary_path(self) -> str | None:
        """The file that identifies this item: the media, or the script for script-only items."""
        return self.media_path or self.script_path

    @property
    def has_script(self) -> bool:
        return bool(self.script_path)

    @property
    def is_script_only(self) -> bool:
        return self.media_path is None


def item_key(item: PlaylistItem) -> str:
    """Identity used for duplicate detection (resolves symlinks / relative paths)."""
    path = item.primary_path or ""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(os.path.abspath(path))


def _natural_key(text: str) -> list:
    # re.split with a capture group alternates str, digit-str, str, ... so
    # positions are type-consistent between any two keys.
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text.lower())]


def _mtime(item: PlaylistItem) -> float:
    path = item.primary_path
    try:
        return os.stat(path).st_mtime if path else 0.0
    except OSError:
        return 0.0


def merge_summary(total: int, added: int, updated: int) -> str:
    skipped = max(0, total - added - updated)
    parts = [f"Added {added} new"]
    if updated:
        parts.append(f"updated {updated} with a newly found script")
    parts.append(f"{skipped} already in playlist")
    return ", ".join(parts)


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

    def add_items(self, items: list[PlaylistItem]) -> tuple[int, int]:
        """Merge items into the playlist without creating duplicates.

        An incoming item whose media file (or script file, for script-only
        items) is already present is skipped -- except that if the existing
        entry has no script and the incoming one does, the script is filled
        in. That is what lets re-scanning a folder pick up scripts that
        appeared after the video was first added.

        Returns (added, updated).
        """
        index = {item_key(i): i for i in self.items}
        added = updated = 0
        for new in items:
            key = item_key(new)
            existing = index.get(key)
            if existing is None:
                self.items.append(new)
                index[key] = new
                added += 1
            elif not existing.script_path and new.script_path:
                existing.script_path = new.script_path
                updated += 1
        if added:
            self._rebuild_order(keep_current=self.current_item())
        return added, updated

    def find(self, item: PlaylistItem) -> int:
        """Index of the playlist entry with the same identity as item, or -1."""
        key = item_key(item)
        for idx, existing in enumerate(self.items):
            if item_key(existing) == key:
                return idx
        return -1

    def sort_items(
        self,
        key: str,
        descending: bool = False,
        duration_of: Callable[[PlaylistItem], float | None] | None = None,
    ) -> bool:
        """Reorder self.items in place. key: manual | modified | name | length.

        Items with an unknown length always sort last for key == "length".
        The currently playing item stays current. Returns True if the order changed.
        """
        if key not in SORT_KEYS or key == "manual" or len(self.items) < 2:
            return False
        before = list(self.items)
        current = self.current_item()

        def name_key(i: PlaylistItem) -> list:
            return _natural_key(Path(i.primary_path).name if i.primary_path else "")

        if key == "name":
            self.items.sort(key=name_key, reverse=descending)
        elif key == "modified":
            mt = {id(i): _mtime(i) for i in self.items}
            self.items.sort(key=lambda i: (mt[id(i)], name_key(i)), reverse=descending)
        else:  # length
            lengths = {id(i): (duration_of(i) if duration_of else None) for i in self.items}
            known = [i for i in self.items if lengths[id(i)] is not None]
            unknown = [i for i in self.items if lengths[id(i)] is None]
            known.sort(key=lambda i: (lengths[id(i)], name_key(i)), reverse=descending)
            unknown.sort(key=name_key)
            self.items[:] = known + unknown

        changed = any(a is not b for a, b in zip(before, self.items))
        if changed:
            self._rebuild_order(keep_current=current)
        return changed

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
        cur_idx = -1
        if keep_current is not None:
            # identity, not equality: items are mutated/reordered in place
            cur_idx = next((i for i, x in enumerate(self.items) if x is keep_current), -1)
        if self.shuffle:
            random.shuffle(self._order)
            if cur_idx >= 0:
                pos_in_order = self._order.index(cur_idx)
                self._order[0], self._order[pos_in_order] = self._order[pos_in_order], self._order[0]
                self._pos = 0
            else:
                self._pos = -1
        else:
            # Sequential order: keep the playhead where the current item
            # actually sits, so "next" is still the item after it.
            self._pos = cur_idx

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
