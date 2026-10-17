from PySide6.QtWidgets import QLabel

from music_desk.ui.main_window import APP_TITLE, MainWindow


def test_main_window_shows(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()
    assert window.windowTitle() == APP_TITLE


def status_bar_texts(window):
    return [label.text() for label in window.statusBar().findChildren(QLabel)] + [window.statusBar().currentMessage()]


def test_rsgain_check_stays_out_of_the_status_bar(qtbot):
    """SPEC §13: no rsgain label; a failed check is explained on the ReplayGain and Tidy cards."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert not any("rsgain" in text for text in status_bar_texts(window))
    window.show_rsgain_version("rsgain 3.6")
    assert not any("rsgain" in text for text in status_bar_texts(window))
    window.show_rsgain_version(None)
    assert not any("rsgain" in text for text in status_bar_texts(window))
    assert "rsgain couldn't run" in window.tools_page.replaygain_card.scope_note.text()
    assert not window.tools_page.replaygain_card.run_button.isEnabled()
    assert not window.tools_page.tidy_card.run_button.isEnabled()
