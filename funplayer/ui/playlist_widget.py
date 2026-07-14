from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from ..playlist import Playlist, PlaylistItem, pair_folder


class PlaylistWidget(QWidget):
    item_activated = Signal(int)          # emits index into playlist.items
    shuffle_toggled = Signal(bool)
    repeat_mode_changed = Signal(str)
    playlist_path_changed = Signal(str)   # emits path whenever a playlist file is saved/loaded

    def __init__(self, playlist: Playlist, parent=None) -> None:
        super().__init__(parent)
        self.playlist = playlist

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.add_files_btn = QPushButton("Add Files")
        self.add_folder_btn = QPushButton("Add Folder")
        self.remove_btn = QPushButton("Remove")
        self.save_btn = QPushButton("Save Playlist")
        self.load_btn = QPushButton("Load Playlist")

        self.shuffle_check = QCheckBox("Shuffle")
        self.shuffle_check.setLayoutDirection(Qt.RightToLeft)
        self.repeat_combo = QComboBox()
        self.repeat_combo.addItems(["off", "one", "all"])

        self.add_files_btn.clicked.connect(self._on_add_files)
        self.add_folder_btn.clicked.connect(self._on_add_folder)
        self.remove_btn.clicked.connect(self._on_remove)
        self.save_btn.clicked.connect(self._on_save)
        self.load_btn.clicked.connect(self._on_load)
        self.shuffle_check.toggled.connect(self.shuffle_toggled.emit)
        self.repeat_combo.currentTextChanged.connect(self.repeat_mode_changed.emit)

        top_row = QHBoxLayout()
        for b in (self.add_files_btn, self.add_folder_btn, self.remove_btn):
            top_row.addWidget(b)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.shuffle_check)
        bottom_row.addWidget(self.repeat_combo)
        bottom_row.addWidget(self.save_btn)
        bottom_row.addWidget(self.load_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.list_widget)
        layout.addLayout(bottom_row)

    def refresh(self) -> None:
        self.list_widget.clear()
        for item in self.playlist.items:
            label = item.name if item.has_script else f"{item.name}  [no script]"
            self.list_widget.addItem(QListWidgetItem(label))

    def highlight_current(self, index: int) -> None:
        self.list_widget.setCurrentRow(index)

    # ---- handlers ----

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self.item_activated.emit(self.list_widget.row(item))

    def _on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add media files")
        if not paths:
            return
        new_items = [PlaylistItem(p, _guess_script(p)) for p in paths]
        self.playlist.add_items(new_items)
        self.refresh()

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder")
        if not folder:
            return
        self.playlist.add_items(pair_folder(folder))
        self.refresh()

    def _on_remove(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self.playlist.remove_at(row)
            self.refresh()

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save playlist", filter="Playlist (*.json)")
        if path:
            self.playlist.save(path)
            self.playlist_path_changed.emit(path)

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load playlist", filter="Playlist (*.json)")
        if not path:
            return
        loaded = Playlist.load(path)
        self.playlist.set_items(loaded.items)
        self.playlist.shuffle = loaded.shuffle
        self.playlist.repeat_mode = loaded.repeat_mode
        self.shuffle_check.setChecked(loaded.shuffle)
        self.repeat_combo.setCurrentText(loaded.repeat_mode)
        self.refresh()
        self.playlist_path_changed.emit(path)


def _guess_script(media_path: str) -> str | None:
    import os

    stem, _ = os.path.splitext(media_path)
    candidate = stem + ".funscript"
    return candidate if os.path.exists(candidate) else None
