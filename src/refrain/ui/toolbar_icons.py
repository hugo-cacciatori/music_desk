"""The toolbars' icons (SPEC §30; the navigation row's and the … button's since SPEC §36 task 3), drawn here
rather than shipped: simple line drawings in the palette's text colour, so they follow light and dark themes.
Drawn large and scaled down by Qt, sharp enough at any DPI.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 64  # the drawing's side; Qt scales it to the toolbar's icon size
NAMES = ("rename", "replaygain", "tidy", "cleanup", "playlist", "advanced_search", "folders", "back", "forward", "up",
         "rescan", "explorer", "more", "search")


def _rename(p: QPainter) -> None:
    """Letters and a text cursor: names made from tags."""
    font = QFont()
    font.setBold(True)
    font.setPixelSize(34)
    p.setFont(font)
    p.drawText(QRectF(2, 8, 44, 44), Qt.AlignmentFlag.AlignCenter, "Aa")
    p.drawLine(QPointF(52, 12), QPointF(52, 52))
    p.drawLine(QPointF(46, 12), QPointF(58, 12))
    p.drawLine(QPointF(46, 52), QPointF(58, 52))


def _replaygain(p: QPainter) -> None:
    """A level meter: bars of rising height, the loudness evened out."""
    for n, height in enumerate((18, 30, 42, 30)):
        x = 8 + n * 13
        p.drawRoundedRect(QRectF(x, 56 - height, 8, height), 2, 2)
    p.drawLine(QPointF(4, 24), QPointF(60, 24))  # the target level


def _star(p: QPainter, cx: float, cy: float, r: float) -> None:
    path = QPainterPath(QPointF(cx, cy - r))
    for x, y in ((cx + r * 0.22, cy - r * 0.22), (cx + r, cy), (cx + r * 0.22, cy + r * 0.22), (cx, cy + r),
                 (cx - r * 0.22, cy + r * 0.22), (cx - r, cy), (cx - r * 0.22, cy - r * 0.22)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _tidy(p: QPainter) -> None:
    """Sparkles: everything put in order at once."""
    _star(p, 26, 36, 22)
    _star(p, 50, 14, 10)
    _star(p, 52, 50, 7)


def _cleanup(p: QPainter) -> None:
    """A broom: a slanted handle and its bristles."""
    p.drawLine(QPointF(52, 6), QPointF(30, 36))
    path = QPainterPath(QPointF(24, 30))
    path.lineTo(QPointF(38, 42))
    path.lineTo(QPointF(26, 58))
    path.lineTo(QPointF(6, 52))
    path.closeSubpath()
    p.drawPath(path)
    for start, end in (((14, 48), (10, 54)), ((20, 50), (16, 56)), ((26, 50), (22, 57))):
        p.drawLine(QPointF(*start), QPointF(*end))


def _playlist(p: QPainter) -> None:
    """A list and a note."""
    for y in (12, 24, 36):
        p.drawLine(QPointF(6, y), QPointF(40 if y == 36 else 52, y))
    p.drawEllipse(QRectF(36, 44, 12, 10))
    p.drawLine(QPointF(48, 49), QPointF(48, 26))
    p.drawLine(QPointF(48, 26), QPointF(58, 30))


def _advanced_search(p: QPainter) -> None:
    """A magnifier over lines: lists of albums to look at."""
    p.drawEllipse(QRectF(6, 6, 36, 36))
    for y in (18, 24, 30):
        p.drawLine(QPointF(15, y), QPointF(33, y))
    pen = p.pen()
    pen.setWidthF(pen.widthF() * 1.6)
    p.setPen(pen)
    p.drawLine(QPointF(37, 37), QPointF(56, 56))


def _folder_outline(p: QPainter, top: float) -> None:
    path = QPainterPath(QPointF(6, 52))
    path.lineTo(6, top)
    path.lineTo(24, top)
    path.lineTo(30, top + 7)
    path.lineTo(58, top + 7)
    path.lineTo(58, 52)
    path.closeSubpath()
    p.drawPath(path)


def _folders(p: QPainter) -> None:
    """A folder: the folder tree."""
    _folder_outline(p, 12)
    p.drawLine(QPointF(6, 27), QPointF(58, 27))


def _arrow(p: QPainter, angle: float) -> None:
    """An arrow pointing right, turned by `angle` degrees."""
    p.save()
    p.translate(32, 32)
    p.rotate(angle)
    p.drawLine(QPointF(-22, 0), QPointF(22, 0))
    p.drawLine(QPointF(22, 0), QPointF(6, -16))
    p.drawLine(QPointF(22, 0), QPointF(6, 16))
    p.restore()


def _back(p: QPainter) -> None:
    _arrow(p, 180)


def _forward(p: QPainter) -> None:
    _arrow(p, 0)


def _up(p: QPainter) -> None:
    _arrow(p, -90)


def _rescan(p: QPainter) -> None:
    """A circling arrow: read again."""
    p.drawArc(QRectF(10, 10, 44, 44), 60 * 16, 300 * 16)
    p.drawLine(QPointF(43, 13), QPointF(44, 4))  # the head, at the arc's start (60 degrees)
    p.drawLine(QPointF(43, 13), QPointF(52, 17))


def _explorer(p: QPainter) -> None:
    """A folder with an arrow leaving it: open elsewhere."""
    path = QPainterPath(QPointF(32, 52))
    path.lineTo(6, 52)
    path.lineTo(6, 14)
    path.lineTo(22, 14)
    path.lineTo(28, 20)
    path.lineTo(32, 20)
    p.drawPath(path)
    p.drawLine(QPointF(30, 40), QPointF(56, 14))
    p.drawLine(QPointF(56, 14), QPointF(40, 14))
    p.drawLine(QPointF(56, 14), QPointF(56, 30))


def _more(p: QPainter) -> None:
    """Three dots: more commands."""
    p.save()
    p.setBrush(p.pen().color())
    for x in (12, 32, 52):
        p.drawEllipse(QPointF(x, 32), 3, 3)
    p.restore()


def _search(p: QPainter) -> None:
    """A magnifier."""
    p.drawEllipse(QRectF(8, 8, 34, 34))
    p.drawLine(QPointF(37, 37), QPointF(56, 56))


DRAW = {"rename": _rename, "replaygain": _replaygain, "tidy": _tidy, "cleanup": _cleanup, "playlist": _playlist,
        "advanced_search": _advanced_search, "folders": _folders, "back": _back, "forward": _forward, "up": _up,
        "rescan": _rescan, "explorer": _explorer, "more": _more, "search": _search}


def tool_icon(name: str, color: QColor) -> QIcon:
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, 4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    DRAW[name](p)
    p.end()
    return QIcon(pixmap)
