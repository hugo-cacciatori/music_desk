"""Ground rule 7 (SPEC §25): the mouse wheel over an input scrolls the page and never changes the value."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTime
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from music_desk.ui.no_wheel import install_no_wheel
from music_desk.ui.qt_util import app


def wheel_down(widget: QWidget) -> None:
    """One notch of the wheel over the middle of `widget`, delivered as Qt delivers it (to the widget, then up)."""
    centre = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(centre, QPointF(widget.mapToGlobal(centre)), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                        False)
    app().sendEvent(widget, event)


def value_of(widget: QWidget) -> object:
    if isinstance(widget, QComboBox):
        return widget.currentIndex()
    if isinstance(widget, QTimeEdit):
        return widget.time()
    return widget.value()  # type: ignore[attr-defined] - spin boxes and sliders


@pytest.fixture
def page(qtbot):
    """A tall scrolling page with one input of each kind, each with room to move both ways."""
    install_no_wheel(app())
    spin = QSpinBox()
    spin.setRange(0, 10)
    spin.setValue(5)
    double = QDoubleSpinBox()
    double.setValue(5)
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])
    combo.setCurrentIndex(1)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 10)
    slider.setValue(5)
    time = QTimeEdit(QTime(0, 5))
    inputs = [spin, double, combo, slider, time]

    content = QWidget()
    layout = QVBoxLayout(content)
    for widget in inputs:
        layout.addWidget(widget)
        layout.addSpacing(400)
    scroll = QScrollArea()
    scroll.setWidget(content)
    scroll.resize(300, 300)
    qtbot.addWidget(scroll)
    scroll.show()
    qtbot.waitExposed(scroll)
    return scroll, inputs


def test_the_wheel_over_an_input_scrolls_the_page_and_keeps_the_value(page):
    scroll, inputs = page
    bar = scroll.verticalScrollBar()
    for widget in inputs:
        scroll.ensureWidgetVisible(widget)
        before, position = value_of(widget), bar.value()
        assert position < bar.maximum()
        wheel_down(widget)
        assert value_of(widget) == before, type(widget).__name__
        assert bar.value() > position, f"the page didn't scroll over a {type(widget).__name__}"


def test_the_wheel_over_a_focused_input_changes_nothing(page):
    _scroll, inputs = page
    for widget in inputs:
        widget.setFocus()
        before = value_of(widget)
        wheel_down(widget)
        assert value_of(widget) == before, type(widget).__name__


def test_the_keyboard_still_changes_the_value(page, qtbot):
    _scroll, inputs = page
    spin = inputs[0]
    spin.setFocus()
    qtbot.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == 6


def test_a_scroll_bar_still_scrolls(page):
    scroll, _inputs = page
    bar = scroll.verticalScrollBar()
    wheel_down(bar)
    assert bar.value() > 0


def test_the_replaygain_fields_in_the_window(window):
    """The real page: the main window installs the filter, so the tools page's number fields keep their values."""
    window.tools.open([])
    card = window.tools_page.replaygain_card
    for field in (card.target, card.threshold, card.margin):
        before = field.value()
        wheel_down(field)
        assert field.value() == before
