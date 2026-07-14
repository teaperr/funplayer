from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..buttplug_client import ButtplugClient, Device
from ..config import Settings
from ..funscript import Funscript
from ..player import MpvPlayer
from ..playlist import MEDIA_EXTS, Playlist, PlaylistItem, pair_folder
from ..sync_engine import SyncEngine
from .calibration_dialog import CalibrationDialog
from .playlist_widget import PlaylistWidget
from .settings_dialog import SettingsDialog


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


# Speed (units/sec) -> color stops for the heatmap gradient. Most funscripts
# spend most of their time in the 0-300 units/s range, so the stops are
# concentrated there rather than spread linearly out to 400+ (which used to
# leave everything looking uniformly green).
_HEATMAP_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0, (40, 80, 190)),      # slow - blue
    (100, (60, 170, 100)),   # green
    (200, (210, 195, 50)),   # yellow
    (320, (230, 120, 30)),   # orange
    (450, (210, 40, 40)),    # red
    (650, (160, 40, 190)),   # purple - extreme
]


def _speed_color(speed: float) -> QColor:
    speed = max(0.0, speed)
    stops = _HEATMAP_STOPS
    if speed <= stops[0][0]:
        return QColor(*stops[0][1])
    for (s0, c0), (s1, c1) in zip(stops, stops[1:]):
        if speed <= s1:
            t = (speed - s0) / max(1.0, (s1 - s0))
            r = int(c0[0] + (c1[0] - c0[0]) * t)
            g = int(c0[1] + (c1[1] - c0[1]) * t)
            b = int(c0[2] + (c1[2] - c0[2]) * t)
            return QColor(r, g, b)
    return QColor(*stops[-1][1])


class HeatmapWidget(QWidget):
    """Script-density heatmap strip, drawn above the seek bar. Click anywhere
    on it to seek, same as the seek bar underneath."""

    seek_requested = Signal(float)  # fraction 0..1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)
        self._script: Funscript | None = None
        self.setCursor(Qt.PointingHandCursor)

    def set_script(self, script: Funscript | None) -> None:
        self._script = script
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        if not self._script or not self._script.actions:
            return
        duration = max(1, self._script.duration_ms)
        w, h = self.width(), self.height()
        actions = self._script.actions
        for i in range(1, len(actions)):
            a, b = actions[i - 1], actions[i]
            speed = abs(b.pos - a.pos) / max(1, (b.at_ms - a.at_ms) / 1000)
            color = _speed_color(speed)
            x0 = int((a.at_ms / duration) * w)
            x1 = max(x0 + 1, int((b.at_ms / duration) * w))
            painter.fillRect(x0, 0, x1 - x0, h, color)

    def mousePressEvent(self, event) -> None:
        if self.width() > 0:
            fraction = max(0.0, min(1.0, event.position().x() / self.width()))
            self.seek_requested.emit(fraction)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle("FunPlayer")
        self.resize(1100, 700)
        self.setAcceptDrops(True)

        self.settings = settings
        self.playlist = Playlist()
        self.playlist.shuffle = settings.shuffle
        self.playlist.repeat_mode = settings.repeat_mode

        self.buttplug = ButtplugClient()
        self.buttplug.connected.connect(self._on_buttplug_connected)
        self.buttplug.devices_changed.connect(self._on_devices_changed)
        self.buttplug.error.connect(self._on_buttplug_error)

        self._player: MpvPlayer | None = None
        self._sync: SyncEngine | None = None
        self._current_script: Funscript | None = None
        self._seeking = False
        self._speed = 1.0

        self._build_ui()
        self._build_menu()
        self._build_shortcuts()

        if self.settings.restore_last_playlist:
            self._try_restore_last_playlist()

        self.buttplug.start()
        if settings.auto_connect_on_start:
            QTimer.singleShot(300, self._connect_buttplug)

    # ---- UI construction ----

    def _build_ui(self) -> None:
        self.video_widget = QWidget()
        self.video_widget.setAttribute(Qt.WA_NativeWindow)
        self.video_widget.setStyleSheet("background-color: black;")

        self.heatmap = HeatmapWidget()
        self.heatmap.seek_requested.connect(self._on_heatmap_seek)

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)

        self.time_label = QLabel("0:00 / 0:00")

        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self._toggle_play)
        self.prev_btn = QPushButton("⏮")
        self.prev_btn.clicked.connect(self._play_previous)
        self.next_btn = QPushButton("⏭")
        self.next_btn.clicked.connect(self._play_next)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.settings.volume)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"])
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(180)
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)

        self.test_pattern_btn = QPushButton("Test Pattern")
        self.test_pattern_btn.clicked.connect(self._run_test_pattern)

        self.status_label = QLabel("Not connected")

        transport = QHBoxLayout()
        transport.addWidget(self.prev_btn)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.next_btn)
        transport.addWidget(self.time_label)
        transport.addWidget(QLabel("Vol:"))
        transport.addWidget(self.volume_slider)
        transport.addWidget(QLabel("Speed:"))
        transport.addWidget(self.speed_combo)
        transport.addStretch(1)
        transport.addWidget(QLabel("Device:"))
        transport.addWidget(self.device_combo)
        transport.addWidget(self.connect_btn)
        transport.addWidget(self.test_pattern_btn)
        transport.addWidget(self.status_label)

        central_layout = QVBoxLayout()
        central_layout.addWidget(self.video_widget, stretch=1)
        central_layout.addWidget(self.heatmap)
        central_layout.addWidget(self.seek_slider)
        central_layout.addLayout(transport)

        central = QWidget()
        central.setLayout(central_layout)
        self.setCentralWidget(central)

        self.playlist_widget = PlaylistWidget(self.playlist)
        self.playlist_widget.item_activated.connect(self._play_index)
        self.playlist_widget.shuffle_toggled.connect(self._on_shuffle_toggled)
        self.playlist_widget.repeat_mode_changed.connect(self._on_repeat_changed)
        self.playlist_widget.shuffle_check.setChecked(self.settings.shuffle)
        self.playlist_widget.repeat_combo.setCurrentText(self.settings.repeat_mode)

        dock = QDockWidget("Playlist", self)
        dock.setWidget(self.playlist_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.playlist_dock = dock

        self.playlist_widget.playlist_path_changed.connect(self._on_playlist_path_changed)

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("File")

        open_file = QAction("Open Media File...", self)
        open_file.triggered.connect(self._open_file)
        menu.addAction(open_file)

        open_folder = QAction("Open Folder (build playlist)...", self)
        open_folder.triggered.connect(self._open_folder)
        menu.addAction(open_folder)

        menu.addSeparator()
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.playlist_dock.toggleViewAction())

        tools_menu = self.menuBar().addMenu("Tools")
        calibrate_action = QAction("Calibrate Offset...", self)
        calibrate_action.triggered.connect(self._open_calibration)
        tools_menu.addAction(calibrate_action)

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self._seek_relative(5))
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self._seek_relative(-5))
        QShortcut(QKeySequence(Qt.Key_Up), self, activated=lambda: self._adjust_volume(5))
        QShortcut(QKeySequence(Qt.Key_Down), self, activated=lambda: self._adjust_volume(-5))
        QShortcut(QKeySequence("N"), self, activated=self._play_next)
        QShortcut(QKeySequence("P"), self, activated=self._play_previous)

    # ---- lifecycle ----

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._player is None:
            self._player = MpvPlayer(self.video_widget, hwdec=self.settings.hwdec)
            self._player.position_changed.connect(self._on_position_changed)
            self._player.duration_changed.connect(self._on_duration_changed)
            self._player.playback_ended.connect(self._on_playback_ended)
            self._player.set_volume(self.settings.volume)
            self._player.speed = self._speed
            self._sync = SyncEngine(self._player, self.buttplug, self.settings)

    def closeEvent(self, event) -> None:
        self.settings.shuffle = self.playlist.shuffle
        self.settings.repeat_mode = self.playlist.repeat_mode
        self.settings.save()
        if self._sync:
            self._sync.stop()
        if self._player:
            self._player.shutdown()
        self.buttplug.shutdown()
        super().closeEvent(event)

    # ---- drag & drop ----

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        new_items: list[PlaylistItem] = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                new_items.extend(pair_folder(pp, fuzzy=self.settings.fuzzy_script_matching))
            elif pp.suffix.lower() in MEDIA_EXTS:
                new_items.append(PlaylistItem(str(pp), _guess_script(str(pp))))
        if new_items:
            was_empty = not self.playlist.items
            self.playlist.add_items(new_items)
            self.playlist_widget.refresh()
            if was_empty:
                self._play_index(0)
        event.acceptProposedAction()

    # ---- file / playlist actions ----

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open media", self.settings.default_media_folder)
        if not path:
            return
        self.playlist.add_items([PlaylistItem(path, _guess_script(path))])
        self.playlist_widget.refresh()
        self._play_index(len(self.playlist.items) - 1)

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open folder", self.settings.default_media_folder)
        if not folder:
            return
        items = pair_folder(folder, fuzzy=self.settings.fuzzy_script_matching)
        self.playlist.set_items(items)
        self.playlist_widget.refresh()
        if items:
            self._play_index(0)

    def _try_restore_last_playlist(self) -> None:
        path = self.settings.last_playlist_path
        if not path or not Path(path).exists():
            return
        try:
            loaded = Playlist.load(path)
        except Exception:
            return
        # Only restore the item list here -- shuffle/repeat state is owned by
        # Settings (see _on_shuffle_toggled/_on_repeat_changed) and was
        # already applied in _build_ui; a playlist file's own embedded
        # shuffle/repeat fields would otherwise silently override that with
        # stale state from whenever the file was last saved.
        self.playlist.set_items(loaded.items)
        self.playlist_widget.refresh()

    def _on_playlist_path_changed(self, path: str) -> None:
        self.settings.last_playlist_path = path
        self.settings.save()

    def _play_index(self, index: int) -> None:
        item = self.playlist.select_index(index)
        if item is None:
            return
        self.playlist_widget.highlight_current(index)
        self._load_item(item)

    def _load_item(self, item: PlaylistItem) -> None:
        if self._player is None:
            return
        self._player.load(item.media_path)
        self._player.play()

        if item.script_path and Path(item.script_path).exists():
            try:
                self._current_script = Funscript.load(item.script_path)
            except Exception as exc:
                self._current_script = None
                QMessageBox.warning(self, "Funscript error", f"Could not parse {item.script_path}:\n{exc}")
        else:
            self._current_script = None

        if self._sync:
            self._sync.load_script(self._current_script)
        self.heatmap.set_script(self._current_script)
        self.setWindowTitle(f"FunPlayer — {item.name}")

    def _play_next(self) -> None:
        item = self.playlist.next()
        if item:
            idx = self.playlist.items.index(item)
            self.playlist_widget.highlight_current(idx)
            self._load_item(item)

    def _play_previous(self) -> None:
        item = self.playlist.previous()
        if item:
            idx = self.playlist.items.index(item)
            self.playlist_widget.highlight_current(idx)
            self._load_item(item)

    def _on_playback_ended(self) -> None:
        if not self.settings.auto_play_next:
            return
        delay = self.settings.inter_video_delay_ms
        if delay > 0:
            QTimer.singleShot(delay, self._play_next)
        else:
            self._play_next()

    def _on_shuffle_toggled(self, enabled: bool) -> None:
        self.playlist.set_shuffle(enabled)
        self.settings.shuffle = enabled
        self.settings.save()

    def _on_repeat_changed(self, mode: str) -> None:
        self.playlist.repeat_mode = mode
        self.settings.repeat_mode = mode
        self.settings.save()

    # ---- transport ----

    def _toggle_play(self) -> None:
        if self._player is None:
            return
        self._player.toggle_pause()
        self.play_btn.setText("Play" if self._player.is_paused else "Pause")

    def _seek_relative(self, delta_seconds: float) -> None:
        if self._player:
            self._player.seek_relative(delta_seconds)

    def _adjust_volume(self, delta: int) -> None:
        self.volume_slider.setValue(max(0, min(100, self.volume_slider.value() + delta)))

    def _on_speed_changed(self, text: str) -> None:
        try:
            value = float(text.rstrip("x"))
        except ValueError:
            return
        self._speed = value
        if self._player:
            self._player.speed = value

    def _on_volume_changed(self, value: int) -> None:
        self.settings.volume = value
        if self._player:
            self._player.set_volume(value)

    def _on_position_changed(self, seconds: float) -> None:
        if self._seeking or self._player is None:
            return
        duration = self._player.duration
        if duration > 0:
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(int((seconds / duration) * 1000))
            self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{_fmt_time(seconds)} / {_fmt_time(duration)}")

    def _on_duration_changed(self, _seconds: float) -> None:
        pass

    def _on_seek_released(self) -> None:
        self._seeking = False
        if self._player is None:
            return
        duration = self._player.duration
        target = (self.seek_slider.value() / 1000) * duration
        self._player.seek(target)

    def _on_heatmap_seek(self, fraction: float) -> None:
        if self._player and self._player.duration > 0:
            self._player.seek(fraction * self._player.duration)

    # ---- buttplug ----

    def _toggle_connection(self) -> None:
        if self.status_label.text().startswith("Connected"):
            self.buttplug.disconnect()
        else:
            self._connect_buttplug()

    def _connect_buttplug(self) -> None:
        self.status_label.setText("Connecting...")
        self.buttplug.connect_to(self.settings.intiface_host, self.settings.intiface_port)

    def _on_buttplug_connected(self, connected: bool) -> None:
        if connected:
            self.status_label.setText("Connected")
            self.connect_btn.setText("Disconnect")
            if self.settings.auto_scan_on_connect:
                self.buttplug.start_scanning()
        else:
            self.status_label.setText("Not connected")
            self.connect_btn.setText("Connect")
            self.device_combo.clear()

    def _on_devices_changed(self, devices: list[Device]) -> None:
        self.device_combo.clear()
        for d in devices:
            self.device_combo.addItem(d.display_name, userData=d)
        if self.settings.preferred_device_name:
            for i in range(self.device_combo.count()):
                d: Device = self.device_combo.itemData(i)
                if d.name == self.settings.preferred_device_name:
                    self.device_combo.setCurrentIndex(i)
                    break

    def _on_device_selected(self, index: int) -> None:
        device = self.device_combo.itemData(index)
        if self._sync:
            self._sync.set_device(device)
        if isinstance(device, Device):
            self.settings.preferred_device_name = device.name

    def _on_buttplug_error(self, message: str) -> None:
        self.status_label.setText("Error")
        QMessageBox.warning(self, "Intiface / Buttplug", message)

    def _run_test_pattern(self) -> None:
        device = self.device_combo.currentData()
        if device is None:
            QMessageBox.information(self, "Test pattern", "Select a device first.")
            return

        sequence = [0.0, 1.0, 0.0, 1.0, 0.0]

        def step(i: int = 0) -> None:
            if i >= len(sequence):
                self.buttplug.stop_device(device.index)
                return
            if device.has_linear:
                self.buttplug.send_linear(device.index, device.linear_feature_index, sequence[i], 400)
            elif device.has_scalar_vibrate:
                self.buttplug.send_scalar_vibrate(device.index, device.scalar_feature_index, sequence[i])
            QTimer.singleShot(450, lambda: step(i + 1))

        step()

    # ---- calibration ----

    def _open_calibration(self) -> None:
        if self._current_script is None or self._player is None:
            QMessageBox.information(self, "Calibrate Offset", "Load a video with a funscript first.")
            return
        dlg = CalibrationDialog(self._current_script, self._player, self)
        if dlg.exec():
            offset = dlg.result_offset_ms()
            if offset is not None:
                self.settings.global_offset_ms = offset
                self.settings.save()
                if self._sync:
                    self._sync.apply_settings(self.settings)
                QMessageBox.information(self, "Calibrate Offset", f"Offset set to {offset} ms.")

    # ---- settings ----

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self.settings = dlg.result_settings()
            self.settings.save()
            if self._sync:
                self._sync.apply_settings(self.settings)
            self.heatmap.setVisible(self.settings.show_script_heatmap)


def _guess_script(media_path: str) -> str | None:
    p = Path(media_path)
    candidate = p.with_suffix(".funscript")
    return str(candidate) if candidate.exists() else None
