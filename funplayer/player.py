from __future__ import annotations

import mpv
from PySide6.QtCore import QTimer, Signal, QObject
from PySide6.QtWidgets import QWidget


class MpvPlayer(QObject):
    """Wraps libmpv, embedding its own GPU-rendered window into `video_widget`
    via X11 window-id (`wid=`). mpv owns its GL/Vulkan context entirely here —
    nothing is shared with Qt's context, which sidesteps the profile/context
    negotiation issues that come up trying to render into a QOpenGLWidget.
    Needs a real X11 window handle, so run under XWayland
    (QT_QPA_PLATFORM=xcb) if you're on Wayland/Hyprland.

    mpv/ffmpeg handles essentially every common video and audio
    container/codec (mp4, mkv, webm, avi, mov, mp3, flac, wav, ...)."""

    position_changed = Signal(float)   # seconds
    duration_changed = Signal(float)   # seconds
    playback_ended = Signal()
    paused_changed = Signal(bool)

    def __init__(self, video_widget: QWidget, hwdec: str = "auto") -> None:
        super().__init__()
        self._mpv = mpv.MPV(
            wid=str(int(video_widget.winId())),
            vo="gpu",
            gpu_context="x11egl",  # force X11/XWayland embedding; libmpv otherwise
                                   # auto-detects WAYLAND_DISPLAY on its own and
                                   # opens a native Wayland surface, ignoring wid=
            hwdec=hwdec,
            keep_open="yes",
            input_default_bindings=False,
            input_vo_keyboard=False,
        )
        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("duration", self._on_duration)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.observe_property("eof-reached", self._on_eof_reached)

        # poll fallback in case property observation misses ticks under some backends
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ---- controls ----

    def load(self, path: str, start: float = 0.0) -> None:
        self._mpv.play(path)
        if start > 0:
            self._mpv.command("seek", start, "absolute")

    def play(self) -> None:
        self._mpv.pause = False

    def pause(self) -> None:
        self._mpv.pause = True

    def toggle_pause(self) -> None:
        self._mpv.pause = not self._mpv.pause

    def stop(self) -> None:
        self._mpv.command("stop")

    def seek(self, seconds: float) -> None:
        self._mpv.command("seek", seconds, "absolute")

    def seek_relative(self, delta_seconds: float) -> None:
        self._mpv.command("seek", delta_seconds, "relative")

    def set_volume(self, volume_0_100: int) -> None:
        self._mpv.volume = volume_0_100

    @property
    def is_paused(self) -> bool:
        try:
            return bool(self._mpv.pause)
        except Exception:
            return True

    @property
    def position(self) -> float:
        try:
            return float(self._mpv.time_pos or 0.0)
        except Exception:
            return 0.0

    @property
    def duration(self) -> float:
        try:
            return float(self._mpv.duration or 0.0)
        except Exception:
            return 0.0

    @property
    def speed(self) -> float:
        try:
            return float(self._mpv.speed or 1.0)
        except Exception:
            return 1.0

    @speed.setter
    def speed(self, value: float) -> None:
        self._mpv.speed = max(0.1, value)

    def shutdown(self) -> None:
        self._poll_timer.stop()
        try:
            self._mpv.terminate()
        except Exception:
            pass

    # ---- internal callbacks ----

    def _on_time_pos(self, _name, value) -> None:
        if value is not None:
            self.position_changed.emit(float(value))

    def _on_duration(self, _name, value) -> None:
        if value is not None:
            self.duration_changed.emit(float(value))

    def _on_pause(self, _name, value) -> None:
        if value is not None:
            self.paused_changed.emit(bool(value))

    def _on_eof_reached(self, _name, value) -> None:
        if value:
            self.playback_ended.emit()

    def _poll(self) -> None:
        # keeps UI position slider smooth even if observe_property lags
        self.position_changed.emit(self.position)
