from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "funplayer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Settings:
    # --- Intiface / Buttplug connection ---
    intiface_host: str = "127.0.0.1"
    intiface_port: int = 12345
    auto_connect_on_start: bool = True
    auto_scan_on_connect: bool = True
    preferred_device_name: str = ""  # remembered device, matched by name on reconnect
    device_mode: str = "auto"  # "auto" | "linear" | "vibrate"

    # --- Sync tuning ---
    global_offset_ms: int = 0       # positive = script fires later (compensates device lag)
    min_position_pct: int = 0       # range clamp, like ScriptPlayer's Range Extender
    max_position_pct: int = 100
    invert_position: bool = False
    max_speed_units_per_s: int = 500  # safety cap on position-change rate, 0 = unlimited
    smoothing_window: int = 1        # moving-average window over actions, 1 = off
    sync_tick_ms: int = 40           # engine loop interval (~25 Hz)
    interp_algorithm: str = "linear"  # "linear" | "smoothstep" | "catmull_rom" | "step"
    command_overlap: float = 1.05    # >1.0 helps real hardware avoid fully stopping between
                                      # commands, at the cost of visible "correction" kinks in
                                      # a pure command-curve view; 1.0 = cleanest curve, no overlap
    vibrate_min_scalar: float = 0.0
    vibrate_max_scalar: float = 1.0

    # --- Playback ---
    volume: int = 100
    hwdec: str = "auto"              # mpv hwdec setting
    remember_position: bool = True
    fuzzy_script_matching: bool = True  # strip trailing "(2019)" etc. when pairing files

    # --- Playlist ---
    shuffle: bool = False
    repeat_mode: str = "off"         # "off" | "one" | "all"
    auto_play_next: bool = True
    inter_video_delay_ms: int = 0    # pause between auto-advancing to the next item
    default_media_folder: str = str(Path.home())
    restore_last_playlist: bool = True
    last_playlist_path: str = ""
    playlists_folder: str = ""       # folder scanned for quick-switch playlists (File > Playlists menu)

    # --- UI ---
    theme: str = "dark"              # "dark" | "light" | "system"
    show_script_heatmap: bool = True

    def save(self) -> None:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "Settings":
        p = config_path()
        if not p.exists():
            s = cls()
            s.save()
            return s
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            defaults = asdict(cls())
            defaults.update({k: v for k, v in data.items() if k in defaults})
            return cls(**defaults)
        except (json.JSONDecodeError, OSError):
            return cls()
