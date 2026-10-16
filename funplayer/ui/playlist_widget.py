from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QHeaderView,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..mediainfo import DurationService, fmt_duration
from ..playlist import SCRIPT_EXTS, Playlist, PlaylistItem, merge_summary, pair_folder

SORT_CHOICES = [
    ("manual", "Manual order"),
    ("modified", "Modified"),
    ("name", "Name"),
    ("length", "Length"),
]


class PlaylistWidget(QWidget):
    item_activated = Signal(int)          # emits index into playlist.items
    playlist_path_changed = Signal(str)   # emits path whenever a playlist file is saved/loaded
    status_message = Signal(str)          # short feedback text for the main window's status bar

    def __init__(self, playlist: Playlist, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.playlist = playlist
        self.settings = settings
        self._filtered_indices: list[int] = []

        self.durations = DurationService(self)
        self.durations.updated.connect(self._on_duration_known)

        # Durations arrive one file at a time; batch the UI updates.
        self._dur_timer = QTimer(self)
        self._dur_timer.setSingleShot(True)
        self._dur_timer.setInterval(250)
        self._dur_timer.timeout.connect(self._on_duration_batch)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search playlist...")
        self.search_box.textChanged.connect(self._render)

        self.sort_combo = QComboBox()
        for key, label in SORT_CHOICES:
            self.sort_combo.addItem(label, key)
        idx = self.sort_combo.findData(settings.playlist_sort_key)
        self.sort_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)

        self.sort_dir_btn = QToolButton()
        self.sort_dir_btn.setCheckable(True)
        self.sort_dir_btn.setChecked(settings.playlist_sort_descending)
        self.sort_dir_btn.toggled.connect(self._on_sort_changed)

        # Two columns: name (stretches) and length (fits its content).
        # Ordering is done by the Sort row, not by clicking the header.
        self.list_widget = QTreeWidget()
        self.list_widget.setColumnCount(2)
        self.list_widget.setHeaderLabels(["Name", "Length"])
        self.list_widget.setRootIsDecorated(False)
        self.list_widget.setUniformRowHeights(True)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setTextElideMode(Qt.ElideMiddle)
        header = self.list_widget.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionsClickable(False)
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

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Sort:"))
        sort_row.addWidget(self.sort_combo, 1)
        sort_row.addWidget(self.sort_dir_btn)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.save_btn)
        bottom_row.addWidget(self.load_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.search_box)
        layout.addLayout(sort_row)
        layout.addWidget(self.list_widget)
        layout.addLayout(bottom_row)

        self._update_sort_controls()

    # ---- sorting ----

    def _sort_key(self) -> str:
        return self.sort_combo.currentData() or "manual"

    def _update_sort_controls(self) -> None:
        manual = self._sort_key() == "manual"
        self.sort_dir_btn.setEnabled(not manual)
        desc = self.sort_dir_btn.isChecked()
        self.sort_dir_btn.setText("\u25BC" if desc else "\u25B2")
        self.sort_dir_btn.setToolTip("Descending" if desc else "Ascending")

    def _on_sort_changed(self, *_args) -> None:
        self.settings.playlist_sort_key = self._sort_key()
        self.settings.playlist_sort_descending = self.sort_dir_btn.isChecked()
        self.settings.save()
        self._update_sort_controls()
        self.refresh()

    def _apply_sort(self) -> bool:
        return self.playlist.sort_items(
            self._sort_key(),
            self.sort_dir_btn.isChecked(),
            duration_of=lambda item: self.durations.get(item.primary_path),
        )

    # ---- rendering ----

    def refresh(self) -> None:
        """Re-sort (if a sort is active), queue length probes, and redraw.

        Call after any change to playlist.items. The chosen sort is sticky,
        so newly added files land in sorted position automatically.
        """
        self.durations.request(i.primary_path for i in self.playlist.items)
        self._apply_sort()
        self._render()

    def _name_text(self, item: PlaylistItem) -> str:
        return item.name if item.has_script else f"{item.name}  [no script]"

    def _length_text(self, item: PlaylistItem) -> str:
        length = self.durations.get(item.primary_path)
        return fmt_duration(length) if length is not None else ""

    def _fill_row(self, row: QTreeWidgetItem, item: PlaylistItem) -> None:
        row.setText(0, self._name_text(item))
        row.setText(1, self._length_text(item))
        row.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)

    def _render(self, *_args) -> None:
        scroll = self.list_widget.verticalScrollBar().value()
        self.list_widget.clear()
        query = self.search_box.text().strip().lower()
        self._filtered_indices = []
        for idx, item in enumerate(self.playlist.items):
            if query and query not in item.name.lower():
                continue
            row = QTreeWidgetItem()
            self._fill_row(row, item)
            self.list_widget.addTopLevelItem(row)
            self._filtered_indices.append(idx)
        current = self.playlist.current_item()
        if current is not None:
            cur_idx = next((i for i, x in enumerate(self.playlist.items) if x is current), -1)
            if cur_idx >= 0:
                self.highlight_current(cur_idx)
        self.list_widget.verticalScrollBar().setValue(scroll)

    def _update_labels(self) -> None:
        for row, idx in enumerate(self._filtered_indices):
            widget_item = self.list_widget.topLevelItem(row)
            if widget_item is not None:
                self._fill_row(widget_item, self.playlist.items[idx])

    def highlight_current(self, index: int) -> None:
        if index in self._filtered_indices:
            row = self.list_widget.topLevelItem(self._filtered_indices.index(index))
            self.list_widget.setCurrentItem(row)
        else:
            # currently-playing item is filtered out of view by the search box
            self.list_widget.clearSelection()

    # ---- duration updates ----

    def _on_duration_known(self, _path: str, _seconds: float) -> None:
        self._dur_timer.start()

    def _on_duration_batch(self) -> None:
        if self._sort_key() == "length" and self._apply_sort():
            self._render()
        else:
            self._update_labels()

    def shutdown(self) -> None:
        self.durations.shutdown()

    # ---- adding ----

    def merge_folder(self, folder: str) -> int:
        """Scan folder and merge it into the current playlist, skipping
        duplicates. Returns the number of newly added items."""
        items = pair_folder(
            folder,
            fuzzy=self.settings.fuzzy_script_matching,
            require_script=self.settings.folder_require_script,
        )
        added, updated = self.playlist.add_items(items)
        self.settings.last_build_folder = folder
        self.settings.save()
        self.refresh()
        self.status_message.emit(merge_summary(len(items), added, updated))
        return added

    # ---- handlers ----

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        row = self.list_widget.indexOfTopLevelItem(item)
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
        added, updated = self.playlist.add_items(new_items)
        self.refresh()
        self.status_message.emit(merge_summary(len(new_items), added, updated))

    def _on_add_folder(self) -> None:
        start = self.settings.last_build_folder or self.settings.default_media_folder
        folder = QFileDialog.getExistingDirectory(self, "Add folder", start)
        if folder:
            self.merge_folder(folder)

    def _on_remove(self) -> None:
        row = self.list_widget.indexOfTopLevelItem(self.list_widget.currentItem())
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
