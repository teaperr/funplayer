from __future__ import annotations

from PySide6.QtCore import QObject, QTimer

from .buttplug_client import ButtplugClient, Device
from .config import Settings
from .funscript import Action, Funscript
from .player import MpvPlayer


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
    """Drives the toy from a funscript + mpv playback position.

    Linear devices are driven event-driven, same core design as before:
    each command is scheduled to land exactly when the device should reach
    its target, chained to the next command as soon as one fires, and every
    call recomputes its position fresh from the real playback time -- so
    seeks/pauses/resumes just self-correct with no separate drift-tracking
    needed for the common case, and dense/fast passages never get
    under-sampled the way a fixed-rate tick would sample them.

    What's new: each real funscript segment (the span between two actual
    script waypoints) is *subdivided* into evenly-timed sub-steps when the
    segment is long enough to benefit -- e.g. a slow 500ms stroke gets
    several smoothly-curved intermediate commands instead of one straight
    linear move. Short/fast segments (typical of dense high-SPM passages)
    naturally collapse to a single sub-step, i.e. exactly the original
    direct point-to-point behavior -- so this never reintroduces the
    fixed-tick under-sampling problem. Sub-step timing is always derived
    from the *real* segment duration, never from an arbitrary tick rate, so
    speed is never artificially forced to a tight/incorrect deadline.

    Interpolation curve used for sub-stepping (settings.interp_algorithm):
      - "linear": straight blend between the two surrounding points.
      - "smoothstep": eased blend, gentler acceleration in/out of each point.
      - "catmull_rom": cubic spline through the surrounding four points.
      - "step": no sub-stepping at all, one command straight to each real
        waypoint (closest to simple on/off-style toys, or for A/B testing
        against the interpolated modes).

    A periodic monitor tick provides a safety net: if actual playback drifts
    more than one script index away from what the in-flight chain expects
    (e.g. a seek landed while a long sub-step's timer was still pending),
    it restarts the chain immediately instead of waiting for the stale
    timer to fire.

    Vibration-only devices are driven from the same monitor tick, since
    there's no "arrival time" concept for a vibration level.
    """

    def __init__(self, player: MpvPlayer, buttplug: ButtplugClient, settings: Settings) -> None:
        super().__init__()
        self._player = player
        self._buttplug = buttplug
        self._settings = settings

        self._raw_actions: list[Action] | None = None
        self._effective: Funscript | None = None
        self._effective_smoothing_window = 1

        self._device: Device | None = None

        self._expected_idx = -2          # base index of the segment our in-flight command belongs to
        self._last_sent_raw_pos: float | None = None
        self._last_vibrate_scalar: float | None = None

        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._schedule_next_linear)

        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(self._monitor_tick)
        self._monitor_timer.start(settings.sync_tick_ms)

        player.paused_changed.connect(self._on_pause_changed)

    def set_device(self, device: Device | None) -> None:
        self._device = device
        self._restart_linear_chain()

    def load_script(self, script: Funscript | None) -> None:
        self._raw_actions = list(script.actions) if script else None
        self._rebuild_effective()
        self._last_vibrate_scalar = None
        self._restart_linear_chain()

    def apply_settings(self, settings: Settings) -> None:
        old_window = self._settings.smoothing_window
        self._settings = settings
        self._monitor_timer.setInterval(settings.sync_tick_ms)
        if settings.smoothing_window != old_window:
            self._rebuild_effective()
            self._restart_linear_chain()

    def _rebuild_effective(self) -> None:
        if self._raw_actions is None:
            self._effective = None
            return
        window = self._settings.smoothing_window
        actions = smoothed_actions(self._raw_actions, window) if window > 1 else list(self._raw_actions)
        self._effective = Funscript(actions=actions)
        self._effective_smoothing_window = window

    def _on_pause_changed(self, paused: bool) -> None:
        if paused:
            self._step_timer.stop()
            if self._device is not None:
                self._buttplug.stop_device(self._device.index)
        else:
            self._restart_linear_chain()

    def _restart_linear_chain(self) -> None:
        self._step_timer.stop()
        self._expected_idx = -2
        self._last_sent_raw_pos = None
        if not self._player.is_paused:
            self._schedule_next_linear()

    def _current_ms(self) -> float:
        return self._player.position * 1000.0 + self._settings.global_offset_ms

    # ---- linear (event-driven chain with curved sub-stepping) ----

    def _curve_value(self, idx: int, prev: Action, nxt: Action, t: float) -> float:
        algo = self._settings.interp_algorithm

        if algo == "smoothstep":
            return prev.pos + (nxt.pos - prev.pos) * _smoothstep(t)

        if algo == "catmull_rom":
            before = self._effective.action_at(idx - 1) or prev
            after = self._effective.action_at(idx + 2) or nxt
            return _catmull_rom(before.pos, prev.pos, nxt.pos, after.pos, t)

        # "linear" and any unrecognized value fall back to a plain blend
        return prev.pos + (nxt.pos - prev.pos) * t

    def _schedule_next_linear(self) -> None:
        if self._effective is None or self._device is None or not self._effective.actions:
            return
        if self._settings.device_mode not in ("auto", "linear") or not self._device.has_linear:
            return
        if self._player.is_paused:
            return

        s = self._settings
        current_ms = self._current_ms()
        idx = self._effective.index_at_or_before(current_ms)
        prev = self._effective.action_at(idx)
        nxt = self._effective.action_at(idx + 1)
        if nxt is None:
            self._expected_idx = idx
            return  # reached end of script, nothing left to schedule

        speed = max(0.1, self._player.speed)

        if prev is None or s.interp_algorithm == "step":
            # nothing to sub-divide against (script hasn't started yet), or
            # sub-stepping disabled: one direct command straight to the
            # real waypoint, exactly like plain point-to-point behavior.
            raw_pos = float(nxt.pos)
            target_at_ms = float(nxt.at_ms)
        else:
            span = max(1, nxt.at_ms - prev.at_ms)
            real_duration_ms = span / speed
            # sub-step granularity aims for roughly sync_tick_ms per step;
            # short/fast segments collapse to a single step naturally.
            num_substeps = max(1, round(real_duration_ms / max(1, s.sync_tick_ms)))
            t_frac = max(0.0, min(1.0, (current_ms - prev.at_ms) / span))
            substep_index = min(num_substeps - 1, int(t_frac * num_substeps))
            target_t = (substep_index + 1) / num_substeps

            if target_t >= 1.0:
                raw_pos = float(nxt.pos)  # land exactly on the real waypoint, no float drift
            else:
                raw_pos = self._curve_value(idx, prev, nxt, target_t)
            target_at_ms = prev.at_ms + target_t * span

        target = self._effective.apply_range(raw_pos, s.min_position_pct, s.max_position_pct, s.invert_position)
        duration_ms = max(20.0, (target_at_ms - current_ms) / speed) * s.command_overlap

        if s.max_speed_units_per_s > 0 and self._last_sent_raw_pos is not None:
            delta_units = abs(raw_pos - self._last_sent_raw_pos)
            min_duration_for_speed = (delta_units / s.max_speed_units_per_s) * 1000
            duration_ms = max(duration_ms, min_duration_for_speed)

        self._buttplug.send_linear(
            self._device.index, self._device.linear_feature_index, target, int(duration_ms)
        )
        self._last_sent_raw_pos = raw_pos
        self._expected_idx = idx

        # fire again exactly when the device should be arriving at this target
        self._step_timer.start(max(1, int(duration_ms)))

    # ---- vibrate + safety-net monitor ----

    def _monitor_tick(self) -> None:
        # pick up live smoothing-window changes without needing a reload
        if self._settings.smoothing_window != self._effective_smoothing_window:
            self._rebuild_effective()
            self._restart_linear_chain()

        if self._effective is None or self._device is None or self._player.is_paused:
            return
        current_ms = self._current_ms()

        if self._settings.device_mode in ("auto", "linear") and self._device.has_linear:
            idx_now = self._effective.index_at_or_before(current_ms)
            if abs(idx_now - self._expected_idx) > 1:
                self._restart_linear_chain()
        elif self._settings.device_mode in ("auto", "vibrate") and self._device.has_scalar_vibrate:
            self._drive_vibrate(current_ms)

    def _drive_vibrate(self, current_ms: float) -> None:
        idx = self._effective.index_at_or_before(current_ms)
        action = self._effective.action_at(idx) or (self._effective.actions[0] if self._effective.actions else None)
        if action is None:
            return
        s = self._settings
        norm = self._effective.apply_range(action.pos, s.min_position_pct, s.max_position_pct, s.invert_position)
        scalar = s.vibrate_min_scalar + norm * (s.vibrate_max_scalar - s.vibrate_min_scalar)

        if self._last_vibrate_scalar is not None and abs(scalar - self._last_vibrate_scalar) < 0.02:
            return
        self._last_vibrate_scalar = scalar
        self._buttplug.send_scalar_vibrate(self._device.index, self._device.scalar_feature_index, scalar)

    def stop(self) -> None:
        self._step_timer.stop()
        if self._device is not None:
            self._buttplug.stop_device(self._device.index)
