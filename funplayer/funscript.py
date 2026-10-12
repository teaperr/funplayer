"""Funscript (.funscript) file parsing.

Format reference: https://pkg.go.dev/github.com/funjack/launchcontrol/protocol/funscript
{
  "version": "1.0",
  "inverted": false,
  "range": 90,
  "actions": [{"pos": 0, "at": 100}, {"pos": 100, "at": 500}, ...]
}
"""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Action:
    at_ms: int
    pos: int  # 0-100


@dataclass
class Funscript:
    actions: list[Action] = field(default_factory=list)
    inverted: bool = False
    range_pct: int = 90
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Funscript":
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_actions = data.get("actions", [])
        actions = sorted(
            (Action(at_ms=int(a["at"]), pos=int(a["pos"])) for a in raw_actions),
            key=lambda a: a.at_ms,
        )
        return cls(
            actions=actions,
            inverted=bool(data.get("inverted", False)),
            range_pct=int(data.get("range", 90)),
            path=path,
        )

    @property
    def duration_ms(self) -> int:
        return self.actions[-1].at_ms if self.actions else 0

    def _times(self) -> list[int]:
        # cached lazily; cheap enough to recompute for typical script sizes
        return [a.at_ms for a in self.actions]

    def index_at_or_before(self, time_ms: float) -> int:
        """Index of the last action whose timestamp is <= time_ms. -1 if none."""
        times = self._times()
        i = bisect.bisect_right(times, time_ms) - 1
        return i

    def next_action(self, time_ms: float) -> Action | None:
        """First action strictly after time_ms, or None at end of script."""
        times = self._times()
        i = bisect.bisect_right(times, time_ms)
        if i >= len(self.actions):
            return None
        return self.actions[i]

    def action_at(self, index: int) -> Action | None:
        if 0 <= index < len(self.actions):
            return self.actions[index]
        return None

    def apply_range(self, pos: int, min_pct: int, max_pct: int, invert: bool) -> float:
        """Map raw 0-100 pos to a 0-1 float, restricting the device's
        physical output to the [min_pct, max_pct] window (like
        ScriptPlayer's Range Extender) while preserving the full script
        curve shape -- not clamping the input, which would only clip the
        extremes and leave the rest of the curve using the device's full
        travel regardless of this setting."""
        p = 100 - pos if invert else pos
        lo, hi = min(min_pct, max_pct), max(min_pct, max_pct)
        scaled = lo + (p / 100.0) * (hi - lo)
        return max(0.0, min(1.0, scaled / 100.0))
