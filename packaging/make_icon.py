"""Draws the app icon and writes resources/icons/music_desk.ico (16 to 256 px) and music_desk.png (256 px).

The icon is drawn with Qt, so it needs no image editor and stays reproducible: a blue rounded square with two
beamed white notes. Run from the project root after changing the drawing:  uv run python packaging/make_icon.py
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources" / "icons"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 256, size / 256)  # drawn on a 256 grid
    small = size <= 24  # thicker strokes and a bigger margin-free tile, so it stays legible

    tile = QRectF(8, 8, 240, 240) if not small else QRectF(0, 0, 256, 256)
    gradient = QLinearGradient(tile.topLeft(), tile.bottomRight())
    gradient.setColorAt(0, QColor("#3b82f6"))
    gradient.setColorAt(1, QColor("#1d4ed8"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(tile, 52, 52)

    white = QColor("#ffffff")
    stem = 22 if not small else 34
    p.setPen(QPen(white, stem, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    left, right, top_left, top_right, bottom = 96, 188, 70, 52, 176
    p.drawLine(QPointF(left, bottom), QPointF(left, top_left + 10))
    p.drawLine(QPointF(right, bottom - 18), QPointF(right, top_right + 10))
    beam = QPainterPath()  # the beam joining the two stems, slanted up to the right
    thickness = 34 if not small else 46
    beam.moveTo(left - stem / 2, top_left)
    beam.lineTo(right + stem / 2, top_right)
    beam.lineTo(right + stem / 2, top_right + thickness)
    beam.lineTo(left - stem / 2, top_left + thickness)
    beam.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawPath(beam)
    for x, y in ((left, bottom), (right, bottom - 18)):  # the note heads, tilted ellipses left of each stem
        p.save()
        p.translate(x - stem / 2 - 22, y)
        p.rotate(-22)
        p.drawEllipse(QRectF(-34, -24, 68, 48) if not small else QRectF(-40, -30, 80, 60))
        p.restore()
    p.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    """An ICO holding one PNG per size (supported by Windows since Vista)."""
    pngs = [png_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    for image, data in zip(images, pngs, strict=True):
        side = image.width() if image.width() < 256 else 0  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(pngs))


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 - QPainter needs one
    OUT.mkdir(parents=True, exist_ok=True)
    images = [draw(size) for size in SIZES]
    write_ico(OUT / "music_desk.ico", images)
    images[-1].save(str(OUT / "music_desk.png"))
    print(f"wrote {OUT / 'music_desk.ico'} ({len(SIZES)} sizes) and music_desk.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
