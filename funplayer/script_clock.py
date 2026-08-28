from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal


class ScriptClock(QObject):
    """A plain wall-clock timer that mimics the parts of MpvPlayer's
    interface SyncEngine and the transport UI actually use (position,
    duration, speed, is_paused, play/pause/toggle_pause/seek/seek_relative,
    set_volume, and the position_changed/duration_changed/playback_ended/
    paused_changed signals) -- so a funscript can be "played" on its own,
    with no accompanying video/audio file, by driving the same sync engine
    and transport controls off this instead of mpv.
    """

    position_changed = Signal(float)
    duration_changed = Signal(float)
    playback_ended = Signal()
    paused_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._duration = 0.0
        self._position = 0.0
        self._paused = True
        self._speed = 1.0
        self._last_tick: float | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30Hz, plenty smooth for a position readout
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---- controls (mirrors MpvPlayer's public surface) ----

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self.duration_changed.emit(self._duration)

    def load(self, _path: str, start: float = 0.0) -> None:
        # no media to load; kept for interface parity, callers should use
        # set_duration() + seek() instead for script-only playback
        self.seek(start)

    def play(self) -> None:
        if self._paused:
            self._paused = False
            self._last_tick = time.monotonic()
            self.paused_changed.emit(False)

    def pause(self) -> None:
        if not self._paused:
            self._advance()
            self._paused = True
            self._last_tick = None
            self.paused_changed.emit(True)

    def toggle_pause(self) -> None:
        self.pause() if not self._paused else self.play()

    def stop(self) -> None:
        self.pause()
        self.seek(0)

    def seek(self, seconds: float) -> None:
        self._position = max(0.0, min(self._duration, seconds))
        if not self._paused:
            self._last_tick = time.monotonic()
        self.position_changed.emit(self._position)

    def seek_relative(self, delta_seconds: float) -> None:
        self._advance()
        self.seek(self._position + delta_seconds)

    def set_volume(self, _volume_0_100: int) -> None:
        pass  # no audio; kept for interface parity with MpvPlayer

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def position(self) -> float:
        return self._position

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._advance()  # settle elapsed time at the old speed before changing it
        self._speed = max(0.1, value)

    # ---- internal ----

    def _advance(self) -> None:
        if self._paused or self._last_tick is None:
            return
        now = time.monotonic()
        elapsed = (now - self._last_tick) * self._speed
        self._last_tick = now
        if elapsed > 0:
            self._position = min(self._duration, self._position + elapsed)

    def _tick(self) -> None:
        if self._paused:
            return
        self._advance()
        self.position_changed.emit(self._position)
        if self._duration > 0 and self._position >= self._duration:
            self.pause()
            self.playback_ended.emit()

    def shutdown(self) -> None:
        self._timer.stop()
