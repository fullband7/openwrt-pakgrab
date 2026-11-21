from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme
from .paint import draw_text

TOPBAR_HEIGHT = 32
TITLE = "OpenWrt PakGrab"
BAR_X = 9
BAR_WIDTH = 14
BAR_Y = (10, 15, 20)
CROSS = 6


class MenuButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.hover = False
        self.setFixedSize(TOPBAR_HEIGHT, TOPBAR_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, active):
        if active != self.active:
            self.active = active
            self.update()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.BG_ITEM if self.hover else theme.BG_BAR))
        pen = QPen(theme.qcolor(theme.TEXT), 2)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        if self.active:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = TOPBAR_HEIGHT / 2
            painter.drawLine(QPointF(center - CROSS, center - CROSS), QPointF(center + CROSS, center + CROSS))
            painter.drawLine(QPointF(center - CROSS, center + CROSS), QPointF(center + CROSS, center - CROSS))
        else:
            for y in BAR_Y:
                painter.drawLine(BAR_X, y + 1, BAR_X + BAR_WIDTH, y + 1)


class TopBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.button = MenuButton(self)
        self.setFixedHeight(TOPBAR_HEIGHT)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.button.move(0, 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.BG_BAR))
        top = (self.height() - theme.CELL_H) // 2
        draw_text(painter, TOPBAR_HEIGHT + 8, top, TITLE, theme.LABEL)


class Scrim(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, 120))
