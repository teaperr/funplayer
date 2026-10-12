from __future__ import annotations

import locale
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "xcb"

from PySide6.QtWidgets import QApplication

from .config import Settings
from .theme import ThemeManager
from .ui.main_window import MainWindow

def main() -> int:
    settings = Settings.load()

    app = QApplication(sys.argv)
    locale.setlocale(locale.LC_NUMERIC, "C")
    app.setApplicationName("FunPlayer")
    theme = ThemeManager(app, settings)
    theme.apply()

    window = MainWindow(settings)
    window.settings_applied.connect(theme.apply)
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
