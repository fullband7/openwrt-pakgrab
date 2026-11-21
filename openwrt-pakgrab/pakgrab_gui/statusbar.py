from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from . import theme
from .paint import draw_text

GAP = "   "


class StatusBar(QWidget):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.parts = list(items)
        self.busy = False
        self.setFixedHeight(theme.STATUS_HEIGHT)

    def set_busy(self, busy):
        if busy != self.busy:
            self.busy = busy
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.BG_BAR))
        top = (self.height() - theme.CELL_H) // 2
        color = theme.ORANGE if self.busy else theme.GREEN
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor(color))
        painter.drawEllipse(QPointF(21.5, self.height() / 2), 3.5, 3.5)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        draw_text(painter, 32, top, "Working" if self.busy else "Ready", theme.LABEL)
        room = (self.width() - 32 - 96) // theme.CELL_W
        parts = list(self.parts)
        while parts and len(GAP.join(parts)) > room:
            parts.pop(0)
        text = GAP.join(parts)
        draw_text(painter, self.width() - 32 - len(text) * theme.CELL_W, top, text, theme.LABEL)
