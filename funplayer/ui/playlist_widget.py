from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..playlist import SCRIPT_EXTS, Playlist, PlaylistItem, pair_folder


class PlaylistWidget(QWidget):
    item_activated = Signal(int)          # emits index into playlist.items
    playlist_path_changed = Signal(str)   # emits path whenever a playlist file is saved/loaded

    def __init__(self, playlist: Playlist, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.playlist = playlist
        self.settings = settings
        self._filtered_indices: list[int] = []

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search playlist...")
        self.search_box.textChanged.connect(self.refresh)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.add_files_btn = QPushButton("Add Files")
        self.add_folder_btn = QPushButton("Add Folder")
        self.remove_btn = QPushButton("Remove")
        self.save_btn = QPushButton("Save Playlist")
        self.load_btn = QPushButton("Load Playlist")

        self.add_files_btn.clicked.connect(self._on_add_files)
        self.add_folder_btn.clicked.connect(self._on_add_folder)
        self.remove_btn.clicked.connect(self._on_remove)
        self.save_btn.clicked.connect(self._on_save)
        self.load_btn.clicked.connect(self._on_load)

        top_row = QHBoxLayout()
        for b in (self.add_files_btn, self.add_folder_btn, self.remove_btn):
            top_row.addWidget(b)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.save_btn)
        bottom_row.addWidget(self.load_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.search_box)
        layout.addWidget(self.list_widget)
        layout.addLayout(bottom_row)

    def refresh(self) -> None:
        self.list_widget.clear()
        query = self.search_box.text().strip().lower()
        self._filtered_indices = []
        for idx, item in enumerate(self.playlist.items):
            if query and query not in item.name.lower():
                continue
            label = item.name if item.has_script else f"{item.name}  [no script]"
            self.list_widget.addItem(QListWidgetItem(label))
            self._filtered_indices.append(idx)

    def highlight_current(self, index: int) -> None:
        if index in self._filtered_indices:
            self.list_widget.setCurrentRow(self._filtered_indices.index(index))
        else:
            # currently-playing item is filtered out of view by the search box
            self.list_widget.clearSelection()

    # ---- handlers ----

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.list_widget.row(item)
        if 0 <= row < len(self._filtered_indices):
            self.item_activated.emit(self._filtered_indices[row])

    def _on_add_files(self) -> None:
        # Accepts media files and lone .funscript files in the same dialog --
        # a selected .funscript becomes a script-only entry directly, no
        # separate "Open Funscript" step needed for the common case of just
        # wanting to queue up a script on its own.
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add files",
            self.settings.default_media_folder,
            filter="Media or Funscript (*.funscript *.mp4 *.mkv *.webm *.avi *.mov *.wmv *.flv *.m4v "
            "*.mpg *.mpeg *.ts *.m2ts *.ogv *.3gp *.mp3 *.flac *.wav *.ogg *.m4a *.aac *.opus *.wma);;"
            "All files (*)",
        )
        if not paths:
            return
        new_items = []
        for p in paths:
            if p.lower().endswith(tuple(SCRIPT_EXTS)):
                new_items.append(PlaylistItem(None, p))
            else:
                new_items.append(PlaylistItem(p, _guess_script(p)))
        self.playlist.add_items(new_items)
        self.refresh()

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add folder", self.settings.default_media_folder)
        if not folder:
            return
        self.playlist.add_items(pair_folder(folder, fuzzy=self.settings.fuzzy_script_matching))
        self.refresh()

    def _on_remove(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._filtered_indices):
            self.playlist.remove_at(self._filtered_indices[row])
            self.refresh()

    def _start_dir(self) -> str:
        return self.settings.playlists_folder or self.settings.default_media_folder

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save playlist", self._start_dir(), filter="Playlist (*.json)")
        if path:
            self.playlist.save(path)
            self.playlist_path_changed.emit(path)

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load playlist", self._start_dir(), filter="Playlist (*.json)")
        if not path:
            return
        # Only the item list is restored here -- shuffle/repeat state is
        # owned by Settings (see MainWindow._on_shuffle_toggled /
        # _on_repeat_changed), not by individual playlist files, to avoid
        # a saved file's stale shuffle/repeat silently overriding the
        # current session state.
        loaded = Playlist.load(path)
        self.playlist.set_items(loaded.items)
        self.refresh()
        self.playlist_path_changed.emit(path)


def _guess_script(media_path: str) -> str | None:
    import os

    stem, _ = os.path.splitext(media_path)
    candidate = stem + ".funscript"
    return candidate if os.path.exists(candidate) else None
