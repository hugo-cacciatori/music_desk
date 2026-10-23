"""The tools toolbar's icons (SPEC §30), drawn here rather than shipped: simple line drawings in the palette's text
colour, so they follow light and dark themes. Drawn large and scaled down by Qt, sharp enough at any DPI.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 64  # the drawing's side; Qt scales it to the toolbar's icon size
NAMES = ("rename", "replaygain", "tidy", "cleanup", "playlist", "advanced_search")


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


DRAW = {"rename": _rename, "replaygain": _replaygain, "tidy": _tidy, "cleanup": _cleanup, "playlist": _playlist,
        "advanced_search": _advanced_search}


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
