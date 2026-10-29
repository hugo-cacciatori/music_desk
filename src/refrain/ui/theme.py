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
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QApplication, QFrame, QPushButton, QWidget

# Spacing steps, on Windows 11's 4 px grid.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 12, 16, 24

# The type scale, as factors of the system's text size (which the theme never changes: Lean UI). The values are
# the ones on screen today; tasks 3 and 4 settle the scale with Hugo as each window is redesigned.
SCALE_CARD_TEXT = 1.1  # a lyrics search card's lines
SCALE_TIDY = 1.15  # the Tidy button's label
SCALE_CARD_TITLE = 1.2  # a lyrics search card's title
SCALE_SEARCH = 1.35  # the search field and its toggle
SCALE_TASK_NAME = 1.15  # a task window's folder or file name, under its title
SCALE_TASK_TITLE = 1.6  # a task window's title (Tidy, Trim…) and an Options page's
OVERLAY_TEXT_PX = 22  # the drop overlay's "Add to library"
ABOUT_TITLE_PX = 16  # the About window's app name

# Windows 11's font, then the one every Windows since Vista has. The whole "Segoe UI Variable" family, not its
# "Text" instance: Qt sees only Regular and Bold in the instance, so semibold text came out bold (Qt 6.12).
FONT_FAMILIES = ("Segoe UI Variable", "Segoe UI")

# The colours Windows doesn't provide, light then dark: a problem's text (Windows 11's critical colour) and an
# invalid field's background.
DANGER = (QColor("#c42b1c"), QColor("#ff99a4"))
INVALID_BACKGROUND = (QColor("#ffcdcd"), QColor("#5c2b2b"))
# Windows 11's layering (SPEC §36 task 3): the window's background under the cards, a card's edge, a control's
# edge, and secondary text (an unselected tab).
LAYER = (QColor("#f3f3f3"), QColor("#202020"))
CARD_BORDER = (QColor("#e5e5e5"), QColor("#383838"))
CONTROL_BORDER = (QColor("#d1d1d1"), QColor("#4c4c4c"))
SECONDARY_TEXT = (QColor("#5f5f5f"), QColor("#c5c5c5"))
CARD_RADIUS, CONTROL_RADIUS = 8, 8
CARD_INSET = 3  # px between a card's edge and its content, so square content leaves the round corners whole


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


def _pick(pair: tuple[QColor, QColor]) -> str:
    return pair[is_dark()].name()


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


def secondary(widget: QWidget) -> None:
    """Text of less weight, in the secondary colour: a full path under its name."""
    widget.setProperty("tone", "secondary")
    _repolish(widget)


def primary(button: QPushButton) -> QPushButton:
    """A window's main action (Run Tidy, Apply renames, Trim…), filled with the accent colour (SPEC §36 task 4)."""
    button.setProperty("primary", True)
    _repolish(button)
    return button


def task_window(window: QWidget) -> None:
    """A task window or dialog on the window background colour, its lists and reports drawn as cards."""
    window.setProperty("taskWindow", True)
    window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    _repolish(window)


def card(widget: QWidget) -> None:
    """A panel drawn as a card: a rounded surface with a thin edge; its lists lose their own frame inside it. A list
    or a text box itself gets the card's edge and a padding instead of an inset."""
    widget.setProperty("card", True)
    if isinstance(widget, QAbstractScrollArea):
        _repolish(widget)
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    widget.setContentsMargins(CARD_INSET, CARD_INSET, CARD_INSET, CARD_INSET)
    for view in widget.findChildren(QAbstractItemView):
        view.setFrameShape(QFrame.Shape.NoFrame)
    _repolish(widget)


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
        # Windows 11's layering: the toolbars and the status bar on the window's background, the panels as cards.
        f"QMainWindow#mainWindow {{ background: {_pick(LAYER)}; }}\n"
        "QMainWindow#mainWindow > QToolBar, QMainWindow#mainWindow > QStatusBar"
        " { background: transparent; border: none; }\n"
        f"QMainWindow#mainWindow > QToolBar {{ padding: 0 {SPACE_XS}px; }}\n"  # in line with the cards' edges
        "QToolButton#moreButton::menu-indicator { image: none; }\n"
        f'[card="true"] {{ background: palette(base); border: 1px solid {_pick(CARD_BORDER)};'
        f" border-radius: {CARD_RADIUS}px; }}\n"
        f'QAbstractScrollArea[card="true"] {{ padding: {CARD_INSET}px; }}\n'
        # Task windows and dialogs (SPEC §36 task 4): the window background, a secondary tone, the accent main action.
        f'[taskWindow="true"] {{ background: {_pick(LAYER)}; }}\n'
        f'[tone="secondary"] {{ color: {_pick(SECONDARY_TEXT)}; }}\n'
        f'QPushButton[primary="true"] {{ background: {accent().name()}; color: {on_accent().name()};'
        f" border: 1px solid {accent().name()}; border-radius: 4px; padding: 5px 16px; }}\n"
        # The Options window: Windows 11's page list (the selected page on a surface, an accent bar beside it), and
        # the pages see-through, their card on the window background.
        "QListWidget#optionsPages { background: transparent; border: none; }\n"
        "QListWidget#optionsPages::item { padding: 6px 10px; margin: 1px 0; border-radius: 4px;"
        " border-left: 3px solid transparent; color: palette(text); }\n"
        f"QListWidget#optionsPages::item:selected {{ background: palette(base); border-left: 3px solid"
        f" {accent().name()}; }}\n"
        f"QListWidget#optionsPages::item:hover:!selected {{ background: {_pick(CARD_BORDER)}; }}\n"
        "QScrollArea#optionsPage, QScrollArea#optionsPage > QWidget > QWidget { background: transparent; }\n"
        f'QPushButton[primary="true"]:hover {{ background: {hover.name()}; }}\n'
        f'QPushButton[primary="true"]:pressed {{ background: {pressed.name()}; }}\n'
        f'QPushButton[primary="true"]:disabled {{ background: {_pick(CONTROL_BORDER)};'
        f" color: {_pick(SECONDARY_TEXT)}; border-color: {_pick(CONTROL_BORDER)}; }}\n"
        # The search toggle joined to the field, which has a magnifier and an accent line under it.
        f"QToolButton#searchMode {{ background: palette(base); border: 1px solid {_pick(CONTROL_BORDER)};"
        " border-right: none; padding: 0 12px; }\n"
        f'QToolButton#searchMode[segment="first"] {{ border-top-left-radius: {CONTROL_RADIUS}px;'
        f" border-bottom-left-radius: {CONTROL_RADIUS}px; }}\n"
        f"QToolButton#searchMode:checked {{ background: {accent().name()}; color: {on_accent().name()};"
        f" border-color: {accent().name()}; }}\n"
        f"QToolButton#searchMode:hover:!checked {{ background: {_pick(LAYER)}; }}\n"
        f"QLineEdit#searchField {{ background: palette(base); border: 1px solid {_pick(CONTROL_BORDER)};"
        f" border-bottom: 2px solid {accent().name()}; border-top-right-radius: {CONTROL_RADIUS}px;"
        f" border-bottom-right-radius: {CONTROL_RADIUS}px; padding: 0 8px; }}\n"
        # The side panel's tabs: plain text, the selected one underlined in the accent colour.
        "QTabWidget#sidePanelTabs::pane { border: none; }\n"
        "QTabWidget#sidePanelTabs::tab-bar { left: 10px; }\n"
        "QTabWidget#sidePanelTabs > QTabBar::tab { background: transparent; border: none;"
        " border-bottom: 2px solid transparent; padding: 6px 2px 4px 2px; margin: 2px 14px 0 0;"
        f" color: {_pick(SECONDARY_TEXT)}; }}\n"
        "QTabWidget#sidePanelTabs > QTabBar::tab:selected { color: palette(text);"
        f" border-bottom: 2px solid {accent().name()}; }}\n"
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
