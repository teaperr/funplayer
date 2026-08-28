from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject

from .buttplug_client import ButtplugClient, Device
from .config import Settings
from .funscript import Action, Funscript


def smoothed_actions(actions: list[Action], window: int) -> list[Action]:
    if window <= 1 or len(actions) < 3:
        return actions
    out = []
    half = window // 2
    for i, a in enumerate(actions):
        lo, hi = max(0, i - half), min(len(actions), i + half + 1)
        avg = sum(x.pos for x in actions[lo:hi]) / (hi - lo)
        out.append(Action(at_ms=a.at_ms, pos=round(avg)))
    return out


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _catmull_rom(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2 * p1)
        + (-p0 + p2) * t
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
        + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    )


class SyncEngine(QObject):
    """Drives the toy from a funscript + a playback clock's position.

    The "player" is anything exposing .position / .speed / .is_paused and a
    paused_changed signal -- MpvPlayer for video/audio items, or ScriptClock
    for funscript-only playback with no accompanying media. set_player()
    lets the active source be swapped at runtime as the playlist moves
    between the two kinds of items.

    Runs entirely on its own background thread, independent of Qt's main
    GUI event loop, so a stalled/throttled GUI/render thread (e.g. some
    compositors throttle unfocused/occluded windows) can't block Buttplug
    command delivery. Buttplug's client is already thread-safe (dispatches
    via asyncio.run_coroutine_threadsafe internally) and libmpv's property
    reads are safe from any thread, so nothing here needs Qt's event loop
    at all -- it only inherits QObject so it can receive paused_changed.

    Linear devices are driven event-driven: each command is scheduled (via
    a monotonic-clock deadline, not a Qt timer) to land exactly when the
    device should reach its target, chained to the next command as soon as
    one is due, and every step recomputes its position fresh from the real
    playback time -- so seeks/pauses/resumes self-correct with no separate
    drift-tracking needed for the common case, and dense/fast passages
    never get under-sampled the way a fixed-rate tick would sample them.

    Each real funscript segment is subdivided into evenly-timed sub-steps
    when the segment is long enough to benefit, shaped by
    settings.interp_algorithm ("linear" / "smoothstep" / "catmull_rom" /
    "step").

    A periodic monitor pass provides a safety net: if actual playback
    drifts more than one script index away from what the in-flight chain
    expects, it restarts the chain immediately instead of waiting for the
    stale deadline.

    Vibration-only devices are driven from the same monitor pass, since
    there's no "arrival time" concept for a vibration level.
    """

    _LOOP_RESOLUTION_S = 0.01  # internal scheduling granularity

    def __init__(self, player, buttplug: ButtplugClient, settings: Settings) -> None:
        super().__init__()
        self._player = player
        self._buttplug = buttplug

        self._lock = threading.Lock()
        self._settings = settings
        self._raw_actions: list[Action] | None = None
        self._effective: Funscript | None = None
        self._effective_smoothing_window = 1
        self._device: Device | None = None
        self._paused = player.is_paused

        self._expected_idx = -2          # base index of the segment our in-flight command belongs to
        self._last_sent_raw_pos: float | None = None
        self._last_vibrate_scalar: float | None = None

        self._chain_dirty = True         # forces an immediate resync on next loop pass
        self._running = True

        player.paused_changed.connect(self._on_pause_changed)

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="SyncEngine")
        self._thread.start()

    # ---- public API (thread-safe: callers are on the Qt main thread) ----

    def set_player(self, player) -> None:
        """Swap the active playback clock (e.g. switching between an
        MpvPlayer-backed item and a ScriptClock-backed script-only item)."""
        try:
            self._player.paused_changed.disconnect(self._on_pause_changed)
        except (RuntimeError, TypeError):
            pass
        self._player = player
        player.paused_changed.connect(self._on_pause_changed)
        with self._lock:
            self._paused = player.is_paused
            self._chain_dirty = True

    def set_device(self, device: Device | None) -> None:
        with self._lock:
            self._device = device
            self._chain_dirty = True

    def load_script(self, script: Funscript | None) -> None:
        with self._lock:
            self._raw_actions = list(script.actions) if script else None
            self._rebuild_effective_locked()
            self._last_vibrate_scalar = None
            self._chain_dirty = True

    def apply_settings(self, settings: Settings) -> None:
        with self._lock:
            old_window = self._settings.smoothing_window
            self._settings = settings
            if settings.smoothing_window != old_window:
                self._rebuild_effective_locked()
                self._chain_dirty = True

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=1.0)
        if self._device is not None:
            self._buttplug.stop_device(self._device.index)

    # ---- internal: must hold self._lock when calling ----

    def _rebuild_effective_locked(self) -> None:
        if self._raw_actions is None:
            self._effective = None
            return
        window = self._settings.smoothing_window
        actions = smoothed_actions(self._raw_actions, window) if window > 1 else list(self._raw_actions)
        self._effective = Funscript(actions=actions)
        self._effective_smoothing_window = window

    def _on_pause_changed(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            self._chain_dirty = True
        if paused and self._device is not None:
            self._buttplug.stop_device(self._device.index)

    def _current_ms(self, settings: Settings) -> float:
        return self._player.position * 1000.0 + settings.global_offset_ms

    # ---- background thread loop ----

    def _run_loop(self) -> None:
        next_linear_deadline = 0.0
        next_monitor_deadline = 0.0

        while self._running:
            now = time.monotonic()

            with self._lock:
                settings = self._settings
                device = self._device
                effective = self._effective
                paused = self._paused
                dirty = self._chain_dirty
                if dirty:
                    self._chain_dirty = False
                    self._last_sent_raw_pos = None
                    self._expected_idx = -2

            if dirty:
                next_linear_deadline = now

            if device is not None and effective is not None and effective.actions and not paused:
                if now >= next_linear_deadline:
                    wait_s = self._schedule_next_linear(effective, device, settings)
                    next_linear_deadline = now + (wait_s if wait_s is not None else self._LOOP_RESOLUTION_S)
            else:
                next_linear_deadline = now

            monitor_interval_s = max(0.01, settings.sync_tick_ms / 1000.0)
            if now >= next_monitor_deadline:
                self._monitor_pass(effective, device, settings, paused)
                next_monitor_deadline = now + monitor_interval_s

            time.sleep(self._LOOP_RESOLUTION_S)

    # ---- linear (event-driven chain with curved sub-stepping) ----

    def _curve_value(self, effective: Funscript, idx: int, prev: Action, nxt: Action, t: float, algo: str) -> float:
        if algo == "smoothstep":
            return prev.pos + (nxt.pos - prev.pos) * _smoothstep(t)

        if algo == "catmull_rom":
            before = effective.action_at(idx - 1) or prev
            after = effective.action_at(idx + 2) or nxt
            return _catmull_rom(before.pos, prev.pos, nxt.pos, after.pos, t)

        # "linear" and any unrecognized value fall back to a plain blend
        return prev.pos + (nxt.pos - prev.pos) * t

    def _schedule_next_linear(self, effective: Funscript, device: Device, s: Settings) -> float | None:
        """Sends the next command if one is due. Returns how many seconds
        to wait before this should be called again (None = nothing to do
        right now, use the default poll resolution)."""
        if s.device_mode not in ("auto", "linear") or not device.has_linear:
            return None

        current_ms = self._current_ms(s)
        idx = effective.index_at_or_before(current_ms)
        prev = effective.action_at(idx)
        nxt = effective.action_at(idx + 1)
        if nxt is None:
            self._expected_idx = idx
            return None  # reached end of script, nothing left to schedule

        speed = max(0.1, self._player.speed)

        if prev is None or s.interp_algorithm == "step":
            raw_pos = float(nxt.pos)
            target_at_ms = float(nxt.at_ms)
        else:
            span = max(1, nxt.at_ms - prev.at_ms)
            real_duration_ms = span / speed
            num_substeps = max(1, round(real_duration_ms / max(1, s.sync_tick_ms)))
            t_frac = max(0.0, min(1.0, (current_ms - prev.at_ms) / span))
            substep_index = min(num_substeps - 1, int(t_frac * num_substeps))
            target_t = (substep_index + 1) / num_substeps

            if target_t >= 1.0:
                raw_pos = float(nxt.pos)  # land exactly on the real waypoint, no float drift
            else:
                raw_pos = self._curve_value(effective, idx, prev, nxt, target_t, s.interp_algorithm)
            target_at_ms = prev.at_ms + target_t * span

        target = effective.apply_range(raw_pos, s.min_position_pct, s.max_position_pct, s.invert_position)
        duration_ms = max(20.0, (target_at_ms - current_ms) / speed) * s.command_overlap

        if s.max_speed_units_per_s > 0 and self._last_sent_raw_pos is not None:
            delta_units = abs(raw_pos - self._last_sent_raw_pos)
            min_duration_for_speed = (delta_units / s.max_speed_units_per_s) * 1000
            duration_ms = max(duration_ms, min_duration_for_speed)

        self._buttplug.send_linear(device.index, device.linear_feature_index, target, int(duration_ms))
        self._last_sent_raw_pos = raw_pos
        self._expected_idx = idx

        return duration_ms / 1000.0

    # ---- vibrate + safety-net monitor ----

    def _monitor_pass(self, effective: Funscript | None, device: Device | None, s: Settings, paused: bool) -> None:
        with self._lock:
            if self._settings.smoothing_window != self._effective_smoothing_window:
                self._rebuild_effective_locked()
                self._chain_dirty = True
                return

        if effective is None or device is None or paused or not effective.actions:
            return
        current_ms = self._current_ms(s)

        if s.device_mode in ("auto", "linear") and device.has_linear:
            idx_now = effective.index_at_or_before(current_ms)
            if abs(idx_now - self._expected_idx) > 1:
                with self._lock:
                    self._chain_dirty = True
        elif s.device_mode in ("auto", "vibrate") and device.has_scalar_vibrate:
            self._drive_vibrate(effective, device, s, current_ms)

    def _drive_vibrate(self, effective: Funscript, device: Device, s: Settings, current_ms: float) -> None:
        idx = effective.index_at_or_before(current_ms)
        action = effective.action_at(idx) or (effective.actions[0] if effective.actions else None)
        if action is None:
            return
        norm = effective.apply_range(action.pos, s.min_position_pct, s.max_position_pct, s.invert_position)
        scalar = s.vibrate_min_scalar + norm * (s.vibrate_max_scalar - s.vibrate_min_scalar)

        if self._last_vibrate_scalar is not None and abs(scalar - self._last_vibrate_scalar) < 0.02:
            return
        self._last_vibrate_scalar = scalar
        self._buttplug.send_scalar_vibrate(device.index, device.scalar_feature_index, scalar)
