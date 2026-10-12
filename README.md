# FunPlayer

Linux equivalent of [ScriptPlayer](https://github.com/FredTungsten/ScriptPlayer)
(that one's Windows/C#-only). Plays video/audio, syncs `.funscript` files to
toys over **Intiface Central** (Buttplug protocol v3), and manages
video+script playlists with shuffle/repeat.

## Features

- Plays every format mpv/ffmpeg supports (mp4, mkv, webm, avi, mov, mp3, flac, wav, ...)
- Buttplug v3 websocket client, talks straight to Intiface Central — no extra deps
- Linear devices (Handy-style) get `LinearCmd` sync; vibration-only toys get
  intensity-mapped `ScalarCmd`
- Settings: connection (host/port/auto-connect/auto-scan), sync (global
  offset, min/max position range, invert, max speed cap, smoothing window,
  tick rate, vibrate intensity range), playback (hwdec, theme, resume,
  heatmap), playlist (fuzzy matching, auto-advance, default folder)
- Playlists pair videos with same-named `.funscript` files (exact or fuzzy
  match, e.g. strips trailing `(2019)`), support shuffle + off/one/all repeat,
  save/load as JSON
- Script heatmap strip above the seek bar (speed-colored)

## Run it (NixOS / home-manager)

Quickest, without editing your home-manager config:

```bash
nix run .#default
```

Add to flake-based home-manager config:

```nix
{
  inputs.funplayer.url = "path:/absolute/path/to/funplayer";

  # in your home-manager module imports:
  imports = [ funplayer.homeManagerModules.default ];
  programs.funplayer.enable = true;
}
```

Or just drop it in `home.packages`:

```nix
home.packages = [ inputs.funplayer.packages.${pkgs.system}.default ];
```

`nix develop` gives you a shell with PySide6/mpv/websockets for hacking on it
directly (`python -m funplayer`).

If your nixpkgs channel doesn't carry `python3Packages.python-mpv`, see the
comment in `flake.nix` for the `buildPythonPackage` fallback.

## Run it without Nix

```bash
pip install PySide6 python-mpv websockets   # needs system libmpv too
python -m funplayer
```

## Using with Intiface Central

1. Open Intiface Central, start the server (default `ws://127.0.0.1:12345`).
2. Launch FunPlayer — it auto-connects/scans by default (toggle in Settings →
   Connection).
3. Pick your device from the dropdown in the transport bar.
4. Load a video with a matching `.funscript` next to it (or build a playlist
   from a folder) and hit play.

## Config

Stored at `~/.config/funplayer/config.json`. Delete it to reset to defaults.

## Known limitations / good next steps

- Resume-playback-position is session-only (not persisted per file yet)
- No E-Stim / DeoVR / VR passthrough (original ScriptPlayer has these)
- Heatmap is a flat speed-based strip, not the fancier gradient view
