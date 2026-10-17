"""Hover texts (SPEC §24): every button and control says what it does when hovered.

The few controls that explain themselves (the explorer views, the address bar, tag fields, OK and Cancel) are
marked with `exempt`, so the guard test (tests/ui/test_hover_texts.py) knows they go without on purpose.
"""

from PySide6.QtWidgets import QWidget

EXEMPT = "hoverExempt"


def exempt(widget: QWidget, why: str) -> None:
    """`widget` and everything inside it need no hover text; `why` is for the reader (and the guard's message)."""
    widget.setProperty(EXEMPT, why)


def is_exempt(widget: QWidget | None) -> bool:
    while widget is not None:
        if widget.property(EXEMPT):
            return True
        widget = widget.parentWidget()
    return False
