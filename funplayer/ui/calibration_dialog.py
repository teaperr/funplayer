from __future__ import annotations

import statistics

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from ..funscript import Funscript
from ..player import MpvPlayer


class CalibrationDialog(QDialog):
    """Tap SPACE in time with the strokes; averages the delta between each
    tap's video position and the nearest scripted action to suggest a
    global_offset_ms. This measures perceived device response lag, not just
    guesswork — play the segment, tap along, click Finish."""

    def __init__(self, script: Funscript, player: MpvPlayer, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate Offset")
        self.setMinimumWidth(380)
        self._script = script
        self._player = player
        self._deltas: list[float] = []
        self._offset_result: int | None = None

        info = QLabel(
            "Play the video, then press SPACE in time with each stroke\n"
            "(or with when you feel/see the toy respond).\n"
            "Collect at least 5 taps, then click Finish."
        )
        info.setWordWrap(True)
        self._count_label = QLabel("Taps recorded: 0")
        self._result_label = QLabel("")

        finish_btn = QPushButton("Finish")
        finish_btn.clicked.connect(self._finish)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        reset_btn = QPushButton("Reset taps")
        reset_btn.clicked.connect(self._reset)

        buttons = QDialogButtonBox()
        buttons.addButton(reset_btn, QDialogButtonBox.ResetRole)
        buttons.addButton(cancel_btn, QDialogButtonBox.RejectRole)
        buttons.addButton(finish_btn, QDialogButtonBox.AcceptRole)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self._count_label)
        layout.addWidget(self._result_label)
        layout.addWidget(buttons)

        self.setFocusPolicy(Qt.StrongFocus)

    def result_offset_ms(self) -> int | None:
        return self._offset_result

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._record_tap()
        else:
            super().keyPressEvent(event)

    def _reset(self) -> None:
        self._deltas.clear()
        self._count_label.setText("Taps recorded: 0")
        self._result_label.setText("")

    def _record_tap(self) -> None:
        if not self._script.actions:
            return
        current_ms = self._player.position * 1000.0
        idx = self._script.index_at_or_before(current_ms)
        candidates = [a for a in (self._script.action_at(idx), self._script.action_at(idx + 1)) if a]
        if not candidates:
            return
        nearest = min(candidates, key=lambda a: abs(a.at_ms - current_ms))
        self._deltas.append(current_ms - nearest.at_ms)
        self._count_label.setText(f"Taps recorded: {len(self._deltas)}")
        if len(self._deltas) >= 2:
            avg = statistics.mean(self._deltas)
            self._result_label.setText(f"Running average offset: {round(avg)} ms")

    def _finish(self) -> None:
        if self._deltas:
            self._offset_result = round(statistics.mean(self._deltas))
        self.accept()
