"""The stall log (SPEC §18): a block of the UI thread over 50 ms is logged. (Not asserting that an idle loop logs
nothing: this machine has rare 150 ms outliers of its own, see SPEC §18.)"""

import logging
import time

from PySide6.QtCore import QTimer

from music_desk.ui.stall_watch import StallWatch


def test_a_stall_is_logged(qtbot, caplog):
    watch = StallWatch()
    with caplog.at_level(logging.WARNING, logger="music_desk.ui.stall_watch"):
        QTimer.singleShot(0, lambda: time.sleep(0.15))
        qtbot.waitUntil(lambda: watch.worst_ms > 0, timeout=3000)
    assert watch.worst_ms >= 100
    assert "the event loop stalled" in caplog.records[-1].getMessage()
