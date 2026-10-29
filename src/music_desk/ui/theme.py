"""The app's look in one place (SPEC §36 task 2): the font, the type scale, the spacing steps, the few colours of
its own, and the one application style sheet. Every window and page takes its look from here, never from a style
sheet of its own.

Light and dark follow Windows, and so does the accent colour: Qt reads both from the system, and `apply` builds
the style sheet again whenever they change, then emits `Theme.changed` for what draws its own pictures (the
toolbar's icons). Widgets carry their state as properties (`tone`, `invalid`) that the style sheet matches, so a
colour change needs no widget to be told.
"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget

# Spacing steps, on Windows 11's 4 px grid.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 12, 16, 24

# The type scale, as factors of the system's text size (which the theme never changes: Lean UI). The values are
# the ones on screen today; tasks 3 and 4 settle the scale with Hugo as each window is redesigned.
SCALE_CARD_TEXT = 1.1  # a lyrics search card's lines
SCALE_TIDY = 1.15  # the Tidy button's label
SCALE_CARD_TITLE = 1.2  # a lyrics search card's title
SCALE_TOOL_TITLE = 1.3  # a tool card's title
SCALE_SEARCH = 1.35  # the search field and its toggle
OVERLAY_TEXT_PX = 22  # the drop overlay's "Add to library"
ABOUT_TITLE_PX = 16  # the About window's app name

# Windows 11's font, then the one every Windows since Vista has. The whole "Segoe UI Variable" family, not its
# "Text" instance: Qt sees only Regular and Bold in the instance, so semibold text came out bold (Qt 6.12).
FONT_FAMILIES = ("Segoe UI Variable", "Segoe UI")

# The colours Windows doesn't provide, light then dark: a problem's text (Windows 11's critical colour) and an
# invalid field's background.
DANGER = (QColor("#c42b1c"), QColor("#ff99a4"))
INVALID_BACKGROUND = (QColor("#ffcdcd"), QColor("#5c2b2b"))


def is_dark() -> bool:
    """Whether the app is dark: Windows' choice when Qt knows it, else the palette's (offscreen, in tests)."""
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme != Qt.ColorScheme.Unknown:
        return scheme == Qt.ColorScheme.Dark
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightnessF() < 0.5


def danger() -> QColor:
    return DANGER[is_dark()]


def invalid_background() -> QColor:
    return INVALID_BACKGROUND[is_dark()]


def accent() -> QColor:
    return QGuiApplication.palette().color(QPalette.ColorRole.Accent)


def on_accent() -> QColor:
    """White or black, whichever reads on the accent colour."""
    return QColor("black") if accent().lightnessF() > 0.6 else QColor("white")


def scaled(font: QFont, factor: float, strong: bool = False) -> QFont:
    """A copy of `font`, `factor` times its size, semibold if `strong`."""
    result = QFont(font)
    result.setPointSizeF(font.pointSizeF() * factor)
    if strong:
        result.setWeight(QFont.Weight.DemiBold)
    return result


def strong(widget: QWidget) -> None:
    """Semibold text: a name, a summary, a card's main action."""
    font = widget.font()
    font.setWeight(QFont.Weight.DemiBold)
    widget.setFont(font)


def section_header(widget: QWidget) -> None:
    """A panel's small header (FOLDERS, PLAYLIST): semibold, letters spaced out."""
    font = widget.font()
    font.setWeight(QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
    widget.setFont(font)


def mark_danger(widget: QWidget, danger: bool = True) -> None:
    """Text that tells of a problem, in the danger colour (or back to the usual colour)."""
    if (widget.property("tone") == "danger") != danger:
        widget.setProperty("tone", "danger" if danger else None)
        _repolish(widget)


def set_invalid(widget: QWidget, invalid: bool) -> None:
    """A field whose value can't be used: its background in the invalid colour until it's fixed."""
    if is_invalid(widget) != invalid:
        widget.setProperty("invalid", invalid)
        _repolish(widget)


def is_invalid(widget: QWidget) -> bool:
    return bool(widget.property("invalid"))


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def style_sheet() -> str:
    """The application's style sheet for the current colours."""
    hover, pressed = accent().lighter(115), accent().darker(115)
    overlay = QColor(accent())
    overlay.setAlpha(60)
    return (
        f'[tone="danger"] {{ color: {danger().name()}; }}\n'
        f'QLineEdit[invalid="true"], QComboBox[invalid="true"], QLabel[invalid="true"]'
        f" {{ background: {invalid_background().name()}; }}\n"
        f"QToolButton#tidyButton {{ background: {accent().name()}; color: {on_accent().name()}; border: none;"
        f" border-radius: 4px; padding: 4px 12px; margin-right: 6px; }}\n"
        f"QToolButton#tidyButton:hover {{ background: {hover.name()}; }}\n"
        f"QToolButton#tidyButton:pressed {{ background: {pressed.name()}; }}\n"
        f"QLabel#dropOverlay {{ background: {overlay.name(QColor.NameFormat.HexArgb)};"
        f" border: 3px dashed {accent().name()}; }}\n"
        "#toast { background: palette(tool-tip-base); border: 1px solid palette(mid); border-radius: 6px; }\n"
    )


class Theme(QObject):
    """Keeps the application's font and style sheet in step with Windows' colours."""

    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._sheet = ""
        app.installEventFilter(self)
        app.styleHints().colorSchemeChanged.connect(self.refresh)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._app and event.type() == QEvent.Type.ApplicationPaletteChange:
            self.refresh()  # the accent colour, or light and dark
        return False

    def refresh(self) -> None:
        sheet = style_sheet()
        if sheet != self._sheet:
            self._sheet = sheet
            self._app.setStyleSheet(sheet)
            self.changed.emit()


_theme: Theme | None = None


def apply(app: QApplication) -> Theme:
    """Gives the application its font and style sheet, once; later calls return the same Theme."""
    global _theme
    if _theme is None:
        families = set(QFontDatabase.families())
        family = next((name for name in FONT_FAMILIES if name in families), None)
        if family is not None:
            font = app.font()
            font.setFamily(family)
            app.setFont(font)
        _theme = Theme(app)
        _theme.refresh()
    return _theme


def current() -> Theme | None:
    """The applied Theme, None before `apply` (the self-test's window, some tests)."""
    return _theme
