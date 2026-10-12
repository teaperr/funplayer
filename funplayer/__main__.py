from __future__ import annotations

import locale
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "xcb"

from PySide6.QtWidgets import QApplication

from .config import Settings
from .ui.main_window import MainWindow

DARK_QSS = """
QWidget { background-color: #232323; color: #e0e0e0; }
QPushButton { background-color: #3a3a3a; border: 1px solid #4a4a4a; padding: 4px 10px; border-radius: 3px; }
QPushButton:hover { background-color: #454545; }
QSlider::groove:horizontal { background: #444; height: 4px; }
QSlider::handle:horizontal { background: #9a9a9a; width: 12px; margin: -5px 0; border-radius: 6px; }
QListWidget, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background-color: #2b2b2b; border: 1px solid #444; }
"""


def main() -> int:
    settings = Settings.load()

    app = QApplication(sys.argv)
    locale.setlocale(locale.LC_NUMERIC, "C")
    app.setApplicationName("FunPlayer")
    if settings.theme == "dark":
        app.setStyleSheet(DARK_QSS)

    window = MainWindow(settings)
    window.show()

    argv_files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if argv_files:
        from .playlist import PlaylistItem
        from .ui.main_window import _guess_script

        items = [PlaylistItem(f, _guess_script(f)) for f in argv_files]
        window.playlist.add_items(items)
        window.playlist_widget.refresh()
        window._play_index(0)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
