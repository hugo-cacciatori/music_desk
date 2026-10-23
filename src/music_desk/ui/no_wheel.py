"""The mouse wheel never changes an input's value (Ground rule 7, SPEC §25).

Scrolling a page while the pointer passes over a number field, a drop-down or a slider used to change its value
by accident. One application-wide filter keeps the wheel from those inputs and hands it to the scroll area around
them, so it scrolls the page; the value still changes by typing, the arrows, the keyboard or the open list.
"""

from typing import cast

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollBar,
    QWidget,
)

INPUTS = (QAbstractSpinBox, QComboBox, QAbstractSlider)


def scroll_areas_around(widget: QWidget):
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            yield parent
        parent = parent.parentWidget()


class NoWheelFilter(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, INPUTS) or isinstance(watched, QScrollBar):
            return False  # a scroll bar's wheel scrolls, which is what it is for
        # Handed on by hand: Qt passes a wheel event up to the parents only when it came from the mouse (a sent
        # one stops at its widget, measured), and this way both behave alike. The nearest scroll area that can
        # still move takes it, as Qt's own propagation would.
        wheel = cast(QWheelEvent, event)
        for area in scroll_areas_around(watched):
            viewport = area.viewport()
            forwarded = QWheelEvent(QPointF(viewport.mapFromGlobal(wheel.globalPosition())), wheel.globalPosition(),
                                    wheel.pixelDelta(), wheel.angleDelta(), wheel.buttons(), wheel.modifiers(),
                                    wheel.phase(), wheel.inverted(), wheel.source())
            QApplication.sendEvent(viewport, forwarded)
            if forwarded.isAccepted():
                break
        event.accept()  # done: Qt doesn't pass it up as well
        return True  # the input never sees it


def install_no_wheel(app: QApplication) -> None:
    """Installs the filter once per application (each main window calls this; tests make several)."""
    if app.findChild(NoWheelFilter) is None:
        app.installEventFilter(NoWheelFilter(app))
