from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self._settings = replace(settings)  # working copy

        tabs = QTabWidget()
        tabs.addTab(self._build_connection_tab(), "Connection")
        tabs.addTab(self._build_sync_tab(), "Sync")
        tabs.addTab(self._build_playback_tab(), "Playback")
        tabs.addTab(self._build_playlist_tab(), "Playlist")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def result_settings(self) -> Settings:
        return self._settings

    # ---- tabs ----

    def _build_connection_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        host = QLineEdit(self._settings.intiface_host)
        host.textChanged.connect(lambda v: setattr(self._settings, "intiface_host", v))
        form.addRow("Intiface host", host)

        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(self._settings.intiface_port)
        port.valueChanged.connect(lambda v: setattr(self._settings, "intiface_port", v))
        form.addRow("Intiface port", port)

        auto_connect = QCheckBox()
        auto_connect.setChecked(self._settings.auto_connect_on_start)
        auto_connect.toggled.connect(lambda v: setattr(self._settings, "auto_connect_on_start", v))
        form.addRow("Auto-connect on start", auto_connect)

        auto_scan = QCheckBox()
        auto_scan.setChecked(self._settings.auto_scan_on_connect)
        auto_scan.toggled.connect(lambda v: setattr(self._settings, "auto_scan_on_connect", v))
        form.addRow("Auto-scan on connect", auto_scan)

        mode = QComboBox()
        mode.addItems(["auto", "linear", "vibrate"])
        mode.setCurrentText(self._settings.device_mode)
        mode.currentTextChanged.connect(lambda v: setattr(self._settings, "device_mode", v))
        form.addRow("Device mode", mode)

        return w

    def _build_sync_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        offset = QSpinBox()
        offset.setRange(-5000, 5000)
        offset.setValue(self._settings.global_offset_ms)
        offset.valueChanged.connect(lambda v: setattr(self._settings, "global_offset_ms", v))
        form.addRow("Global offset (ms)", offset)

        min_pos = QSpinBox()
        min_pos.setRange(0, 100)
        min_pos.setValue(self._settings.min_position_pct)
        min_pos.valueChanged.connect(lambda v: setattr(self._settings, "min_position_pct", v))
        form.addRow("Min position %", min_pos)

        max_pos = QSpinBox()
        max_pos.setRange(0, 100)
        max_pos.setValue(self._settings.max_position_pct)
        max_pos.valueChanged.connect(lambda v: setattr(self._settings, "max_position_pct", v))
        form.addRow("Max position %", max_pos)

        invert = QCheckBox()
        invert.setChecked(self._settings.invert_position)
        invert.toggled.connect(lambda v: setattr(self._settings, "invert_position", v))
        form.addRow("Invert position", invert)

        max_speed = QSpinBox()
        max_speed.setRange(0, 5000)
        max_speed.setValue(self._settings.max_speed_units_per_s)
        max_speed.valueChanged.connect(lambda v: setattr(self._settings, "max_speed_units_per_s", v))
        form.addRow("Max speed (units/s, 0=off)", max_speed)

        smoothing = QSpinBox()
        smoothing.setRange(1, 15)
        smoothing.setValue(self._settings.smoothing_window)
        smoothing.valueChanged.connect(lambda v: setattr(self._settings, "smoothing_window", v))
        form.addRow("Smoothing window", smoothing)

        tick = QSpinBox()
        tick.setRange(10, 200)
        tick.setValue(self._settings.sync_tick_ms)
        tick.valueChanged.connect(lambda v: setattr(self._settings, "sync_tick_ms", v))
        form.addRow("Sync interval (ms)", tick)

        interp = QComboBox()
        interp.addItems(["linear", "smoothstep", "catmull_rom", "step"])
        interp.setCurrentText(self._settings.interp_algorithm)
        interp.currentTextChanged.connect(lambda v: setattr(self._settings, "interp_algorithm", v))
        form.addRow("Interpolation", interp)

        overlap = QDoubleSpinBox()
        overlap.setRange(1.0, 2.0)
        overlap.setSingleStep(0.05)
        overlap.setValue(self._settings.command_overlap)
        overlap.setToolTip(
            "1.0 = cleanest curve, no command overlap.\n"
            "Higher values help real hardware avoid fully stopping between\n"
            "updates, at the cost of visible correction kinks if you're\n"
            "inspecting the raw curve (e.g. Intiface's simulated stroker)."
        )
        overlap.valueChanged.connect(lambda v: setattr(self._settings, "command_overlap", v))
        form.addRow("Command overlap", overlap)

        vib_min = QDoubleSpinBox()
        vib_min.setRange(0.0, 1.0)
        vib_min.setSingleStep(0.05)
        vib_min.setValue(self._settings.vibrate_min_scalar)
        vib_min.valueChanged.connect(lambda v: setattr(self._settings, "vibrate_min_scalar", v))
        form.addRow("Vibrate min intensity", vib_min)

        vib_max = QDoubleSpinBox()
        vib_max.setRange(0.0, 1.0)
        vib_max.setSingleStep(0.05)
        vib_max.setValue(self._settings.vibrate_max_scalar)
        vib_max.valueChanged.connect(lambda v: setattr(self._settings, "vibrate_max_scalar", v))
        form.addRow("Vibrate max intensity", vib_max)

        return w

    def _build_playback_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        hwdec = QComboBox()
        hwdec.addItems(["auto", "no", "vaapi", "nvdec"])
        hwdec.setCurrentText(self._settings.hwdec)
        hwdec.currentTextChanged.connect(lambda v: setattr(self._settings, "hwdec", v))
        form.addRow("Hardware decoding", hwdec)

        remember = QCheckBox()
        remember.setChecked(self._settings.remember_position)
        remember.toggled.connect(lambda v: setattr(self._settings, "remember_position", v))
        form.addRow("Remember playback position", remember)

        theme = QComboBox()
        theme.addItems(["dark", "light", "system"])
        theme.setCurrentText(self._settings.theme)
        theme.currentTextChanged.connect(lambda v: setattr(self._settings, "theme", v))
        form.addRow("Theme", theme)

        heatmap = QCheckBox()
        heatmap.setChecked(self._settings.show_script_heatmap)
        heatmap.toggled.connect(lambda v: setattr(self._settings, "show_script_heatmap", v))
        form.addRow("Show script heatmap", heatmap)

        return w

    def _build_playlist_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        fuzzy = QCheckBox()
        fuzzy.setChecked(self._settings.fuzzy_script_matching)
        fuzzy.toggled.connect(lambda v: setattr(self._settings, "fuzzy_script_matching", v))
        form.addRow("Fuzzy script/video matching", fuzzy)

        auto_next = QCheckBox()
        auto_next.setChecked(self._settings.auto_play_next)
        auto_next.toggled.connect(lambda v: setattr(self._settings, "auto_play_next", v))
        form.addRow("Auto-play next item", auto_next)

        delay = QSpinBox()
        delay.setRange(0, 30000)
        delay.setSingleStep(500)
        delay.setSuffix(" ms")
        delay.setValue(self._settings.inter_video_delay_ms)
        delay.valueChanged.connect(lambda v: setattr(self._settings, "inter_video_delay_ms", v))
        form.addRow("Delay before next item", delay)

        restore_playlist = QCheckBox()
        restore_playlist.setChecked(self._settings.restore_last_playlist)
        restore_playlist.toggled.connect(lambda v: setattr(self._settings, "restore_last_playlist", v))
        form.addRow("Reopen last playlist on start", restore_playlist)

        folder = QLineEdit(self._settings.default_media_folder)
        folder.textChanged.connect(lambda v: setattr(self._settings, "default_media_folder", v))
        form.addRow("Default media folder", folder)

        playlists_folder_row = QHBoxLayout()
        playlists_folder = QLineEdit(self._settings.playlists_folder)
        playlists_folder.setPlaceholderText("Folder to scan for quick-switch playlists")
        playlists_folder.textChanged.connect(lambda v: setattr(self._settings, "playlists_folder", v))
        browse_btn = QPushButton("Browse...")

        def _browse() -> None:
            chosen = QFileDialog.getExistingDirectory(
                self, "Playlists folder", playlists_folder.text() or self._settings.default_media_folder
            )
            if chosen:
                playlists_folder.setText(chosen)

        browse_btn.clicked.connect(_browse)
        playlists_folder_row.addWidget(playlists_folder)
        playlists_folder_row.addWidget(browse_btn)
        form.addRow("Playlists folder", playlists_folder_row)

        return w
