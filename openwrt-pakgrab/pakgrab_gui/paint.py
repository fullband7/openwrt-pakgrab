from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from . import theme

PARTIAL = {
    "▏": 1,
    "▎": 2,
    "▍": 3,
    "▌": 4,
    "▋": 5,
    "▊": 6,
    "▉": 7,
    "█": 8,
}
SHADE = "░"
BRAILLE_DOTS = (
    (0x01, 0, 0),
    (0x02, 0, 1),
    (0x04, 0, 2),
    (0x40, 0, 3),
    (0x08, 1, 0),
    (0x10, 1, 1),
    (0x20, 1, 2),
    (0x80, 1, 3),
)


def _block(painter, x, y, char, color):
    top = y + 4
    height = theme.CELL_H - 8
    if char == SHADE:
        shade = QColor(theme.qcolor(color))
        shade.setAlpha(80)
        painter.fillRect(x, top, theme.CELL_W, height, shade)
    else:
        painter.fillRect(x, top, theme.CELL_W * PARTIAL[char] // 8, height, theme.qcolor(color))


def _braille(painter, x, y, char, color):
    bits = ord(char) - 0x2800
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.qcolor(color))
    for bit, column, row in BRAILLE_DOTS:
        if bits & bit:
            painter.drawEllipse(QPointF(x + 2.5 + column * 3, y + 3.5 + row * 3), 1.1, 1.1)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)


def draw_text(painter, x, y, text, color, bold=False):
    if not text.strip():
        return
    normal, heavy, baseline = theme.fonts()
    painter.setFont(heavy if bold else normal)
    painter.setPen(theme.qcolor(color))
    if text.isascii():
        painter.drawText(x, y + baseline, text)
        return
    for offset, char in enumerate(text):
        cell = x + offset * theme.CELL_W
        if char in PARTIAL or char == SHADE:
            _block(painter, cell, y, char, color)
        elif "⠀" <= char <= "⣿":
            _braille(painter, cell, y, char, color)
            painter.setPen(theme.qcolor(color))
        elif char.isascii():
            painter.drawText(cell, y + baseline, char)
        else:
            painter.drawText(QRectF(cell, y, theme.CELL_W, theme.CELL_H), Qt.AlignmentFlag.AlignCenter, char)
