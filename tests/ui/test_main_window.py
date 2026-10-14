from music_desk.ui.main_window import APP_TITLE, MainWindow


def test_main_window_shows(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()
    assert window.windowTitle() == APP_TITLE


def test_rsgain_status(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_rsgain_version("rsgain 3.6")
    assert window.rsgain_label.text() == "rsgain 3.6"
    window.show_rsgain_version(None)
    assert "unavailable" in window.rsgain_label.text()
