"""Theme handling: "dark", "light", or "system" (follow the desktop).

The app runs under XWayland (QT_QPA_PLATFORM=xcb, needed for mpv embedding),
where Qt does not learn the desktop's dark/light preference on its own. So
for "system" the preference is read directly, trying in order:

1. Qt's own style hints (works if a platform theme supplies it)
2. the freedesktop settings portal (org.appearance color-scheme)
3. gsettings (org.gnome.desktop.interface color-scheme / gtk-theme)

and re-checked every couple of seconds so a live switch is picked up.
"""
from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication

from .config import Settings

DARK_QSS = """
QWidget { background-color: #232323; color: #e0e0e0; }
QPushButton { background-color: #3a3a3a; border: 1px solid #4a4a4a; padding: 4px 10px; border-radius: 3px; }
QPushButton:hover { background-color: #454545; }
QSlider::groove:horizontal { background: #444; height: 4px; }
QSlider::handle:horizontal { background: #9a9a9a; width: 12px; margin: -5px 0; border-radius: 6px; }
QListWidget, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background-color: #2b2b2b; border: 1px solid #444; }
"""


def _scheme_from_qt() -> str | None:
    try:
        scheme = QApplication.instance().styleHints().colorScheme()
    except AttributeError:  # Qt < 6.5
        return None
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return None


def _scheme_from_portal() -> str | None:
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        iface = QDBusInterface(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            bus,
        )
        if not iface.isValid():
            return None
        iface.setTimeout(1000)
        reply = iface.call("Read", "org.freedesktop.appearance", "color-scheme")
        args = reply.arguments()
        if not args:
            return None
        value = args[0]
        while hasattr(value, "variant"):  # reply is a variant wrapped in a variant
            value = value.variant()
        # 0 = no preference, 1 = prefer dark, 2 = prefer light
        return {1: "dark", 2: "light"}.get(int(value))
    except Exception:
        return None


def _gsettings(key: str) -> str | None:
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", key],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip().strip("'").lower() if out.returncode == 0 else None


def _scheme_from_gsettings() -> str | None:
    value = _gsettings("color-scheme")
    if value == "prefer-dark":
        return "dark"
    if value in ("prefer-light", "default"):
        # "default" means no preference; fall through to the GTK theme name
        theme = _gsettings("gtk-theme") or ""
        return "dark" if "dark" in theme else "light"
    return None


class ThemeManager(QObject):
    def __init__(self, app: QApplication, settings: Settings) -> None:
        super().__init__(app)
        self._app = app
        self._settings = settings
        self._applied: str | None = None
        self._backend = None  # detector that last gave an answer; polled alone afterwards
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)

    def apply(self) -> None:
        """Apply settings.theme. Call at startup and after settings change."""
        mode = self._settings.theme
        if mode == "system":
            self._backend = None
            self._set_scheme(self._detect())
            self._timer.start()
        else:
            self._timer.stop()
            self._set_scheme("dark" if mode == "dark" else "light")

    def _detect(self) -> str | None:
        if self._backend is not None:
            result = self._backend()
            if result is not None:
                return result
            self._backend = None
        for backend in (_scheme_from_qt, _scheme_from_portal, _scheme_from_gsettings):
            result = backend()
            if result is not None:
                self._backend = backend
                return result
        return None

    def _poll(self) -> None:
        self._set_scheme(self._detect())

    def _set_scheme(self, scheme: str | None) -> None:
        # None = undetectable: keep whatever is applied (Qt's default is light).
        if scheme is None or scheme == self._applied:
            return
        self._app.setStyleSheet(DARK_QSS if scheme == "dark" else "")
        self._applied = scheme
