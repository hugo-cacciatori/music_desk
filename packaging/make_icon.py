"""Draws the app icon, resources/icons/music_desk.ico (16 to 256 px) and music_desk.png (256 px), and, with
--wizard <folder>, the installer's wizard images (build_installer.py makes them in build/wizard, never committed).

The logo (SPEC §36) is the repeat sign, the score mark for a refrain: a thick bar, a thin bar and two dots, white on
a coral rounded square. Drawn with Qt, so it needs no image editor and stays reproducible. The sizes up to 32 px
and 40 and 48 px are laid out by hand on the pixel grid, so every edge is sharp; larger sizes scale the 64-unit master.
Run from the project root after changing the drawing:  uv run python packaging/make_icon.py
"""

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources" / "icons"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
CORAL = QColor("#E8613C")
WHITE = QColor("#ffffff")

# Inno Setup's wizard image sizes, 100% to 250% display scaling (its WizardImageFile documentation).
WIZARD_LARGE = ((164, 314), (192, 386), (246, 459), (273, 556), (328, 604), (355, 700), (410, 797))
WIZARD_SMALL = ((55, 58), (64, 68), (83, 80), (92, 97), (110, 106), (119, 123), (138, 140))


@dataclass(frozen=True)
class Layout:
    """The sign's parts as (left, top, right, bottom) rectangles, in pixels or in master units."""

    tile: tuple[float, float, float, float]
    radius: float
    thick: tuple[float, float, float, float]
    thin: tuple[float, float, float, float]
    dots: tuple[tuple[float, float, float, float], tuple[float, float, float, float]]


MASTER = Layout(  # on a 64-unit grid, as in docs/identity/logo-concepts/b-repeat-sign.svg
    tile=(4, 4, 60, 60), radius=12, thick=(17, 15, 26, 49), thin=(30, 15, 34.5, 49),
    dots=((38.5, 21.5, 47.5, 30.5), (38.5, 33.5, 47.5, 42.5)),
)
# Hand-placed on the pixel grid: the tile fills the square (no margin to waste), parts on whole pixels.
PIXEL = {
    16: Layout((0, 0, 16, 16), 3, (4, 3, 6, 13), (7, 3, 8, 13), ((10, 5, 12, 7), (10, 9, 12, 11))),
    20: Layout((0, 0, 20, 20), 4, (5, 4, 8, 16), (9, 4, 10, 16), ((12, 6, 15, 9), (12, 11, 15, 14))),
    24: Layout((0, 0, 24, 24), 5, (6, 5, 9, 19), (11, 5, 13, 19), ((15, 7, 18, 10), (15, 14, 18, 17))),
    32: Layout((1, 1, 31, 31), 6, (8, 8, 12, 24), (14, 8, 16, 24), ((20, 11, 24, 15), (20, 17, 24, 21))),
    40: Layout((2, 2, 38, 38), 8, (10, 9, 15, 31), (18, 9, 21, 31), ((24, 13, 30, 19), (24, 21, 30, 27))),
    48: Layout((3, 3, 45, 45), 9, (12, 11, 19, 37), (22, 11, 25, 37), ((28, 15, 36, 23), (28, 25, 36, 33))),
}


def rect(r: tuple[float, float, float, float]) -> QRectF:
    return QRectF(r[0], r[1], r[2] - r[0], r[3] - r[1])


def paint_sign(p: QPainter, layout: Layout, tile: bool = True) -> None:
    """Paints the layout in the painter's current coordinates; square dots below 4 px, which read better."""
    p.setPen(Qt.PenStyle.NoPen)
    if tile:
        p.setBrush(CORAL)
        p.drawRoundedRect(rect(layout.tile), layout.radius, layout.radius)
    p.setBrush(WHITE)
    for bar in (layout.thick, layout.thin):
        p.drawRect(rect(bar))
    for dot in layout.dots:
        if dot[2] - dot[0] < 4:
            p.drawRect(rect(dot))
        else:
            p.drawEllipse(rect(dot))


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if size in PIXEL:
        paint_sign(p, PIXEL[size])
    else:
        p.scale(size / 64, size / 64)
        paint_sign(p, MASTER)
    p.end()
    return image


def wizard_large(width: int, height: int) -> QImage:
    """The welcome page's side image: coral, with the white sign centred in the upper part."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(CORAL)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = width * 0.9  # the master's tile is 56 of 64 units, so the sign itself spans about half the width
    p.translate((width - side) / 2, height * 0.38 - side / 2)
    p.scale(side / 64, side / 64)
    paint_sign(p, MASTER, tile=False)
    p.end()
    return image


def wizard_small(width: int, height: int) -> QImage:
    """The inner pages' corner image: the icon on the wizard's white header."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(WHITE)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(width, height)
    p.translate((width - side) / 2, (height - side) / 2)
    p.scale(side / 64, side / 64)
    paint_sign(p, MASTER)
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


def wizard_files(kind: str) -> list[str]:
    """The wizard images' file names, smallest first, as installer.iss lists them."""
    sizes = WIZARD_LARGE if kind == "large" else WIZARD_SMALL
    return [f"{kind}-{w}x{h}.bmp" for w, h in sizes]


def write_wizard(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for (w, h), name in zip(WIZARD_LARGE, wizard_files("large"), strict=True):
        wizard_large(w, h).save(str(folder / name), "BMP")
    for (w, h), name in zip(WIZARD_SMALL, wizard_files("small"), strict=True):
        wizard_small(w, h).save(str(folder / name), "BMP")


def main(argv: list[str]) -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 - QPainter needs one
    if "--wizard" in argv:
        folder = Path(argv[argv.index("--wizard") + 1])
        write_wizard(folder)
        print(f"wrote {len(WIZARD_LARGE) + len(WIZARD_SMALL)} wizard images to {folder}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    images = [draw(size) for size in SIZES]
    write_ico(OUT / "music_desk.ico", images)
    images[-1].save(str(OUT / "music_desk.png"))
    print(f"wrote {OUT / 'music_desk.ico'} ({len(SIZES)} sizes) and music_desk.png")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
