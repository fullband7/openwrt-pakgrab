from PySide6.QtCore import QPointF, QRectF, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import QWidget

from pakgrab import __version__
from . import theme
from .paint import draw_text

OFF_STATE, READY_STATE, FAILED_STATE = 0, 1, 2
INDENT_ROOT = 1
INDENT_DOT = 2
INDENT_NAME = 4
INDENT_VALUE = 11
DOT_RADIUS = 3.2
HEADER_GAP = 6
LINK_TEXT = "GitHub Page"
LINK_URL = "https://github.com/fullband7/openwrt-pakgrab"
VERSION_TEXT = "ver " + __version__
VERSION_GAP = 4
LINK_ICON = 12
LINK_SPACE = 6
FOOTER_MARGIN = 16
LINK_DROP = 1


class SideBar(QWidget):
    def __init__(self, root, feeds, session, parent=None):
        super().__init__(parent)
        self.root = root
        self.feeds = list(feeds)
        self.session = list(session)
        self.states = {}
        self.offset = 0
        self.overlay = False
        self.hover = False
        self.setMouseTracking(True)
        self.setFixedWidth(theme.SIDEBAR_COLS * theme.CELL_W)
        self.setMinimumHeight(theme.PAD_TOP + 10 * theme.CELL_H + self._footer())

    @staticmethod
    def _footer():
        return theme.CELL_H * 2 + VERSION_GAP + FOOTER_MARGIN * 2

    def set_feed(self, name, ok):
        self.states[name] = READY_STATE if ok else FAILED_STATE
        self.update()

    def _rows(self):
        rows = [("header", "FOLDERS"), ("root", self.root)]
        rows.extend(("feed", name) for name in self.feeds)
        rows.append(("blank",))
        rows.append(("header", "SESSION"))
        rows.extend(("info", label, value) for label, value in self.session)
        return rows

    def _limit(self):
        return self.height() - self._footer()

    def _visible(self):
        return max(1, (self._limit() - theme.PAD_TOP) // theme.CELL_H)

    def wheelEvent(self, event):
        rows = len(self._rows())
        step = -1 if event.angleDelta().y() > 0 else 1
        self.offset = max(0, min(max(0, rows - self._visible()), self.offset + step))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.offset = max(0, min(self.offset, len(self._rows()) - self._visible()))

    def _fit(self, text, indent):
        room = self.width() // theme.CELL_W - theme.PAD_X // theme.CELL_W - indent - 1
        return text if len(text) <= room else text[:room - 1] + "…"

    def _link_rect(self):
        width = len(LINK_TEXT) * theme.CELL_W + LINK_SPACE + LINK_ICON
        return QRectF(theme.PAD_X, self.height() - FOOTER_MARGIN - theme.CELL_H * 2 - VERSION_GAP, width, theme.CELL_H)

    def mouseMoveEvent(self, event):
        inside = self._link_rect().contains(event.position())
        if inside != self.hover:
            self.hover = inside
            self.setCursor(Qt.CursorShape.PointingHandCursor if inside else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        if self.hover:
            self.hover = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._link_rect().contains(event.position()):
            QDesktopServices.openUrl(QUrl(LINK_URL))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.BG_SIDE))
        cell = theme.CELL_W
        left = theme.PAD_X
        limit = self._limit()
        y = theme.PAD_TOP
        for row in self._rows()[self.offset:]:
            if y + theme.CELL_H > limit:
                break
            kind = row[0]
            if kind == "header":
                draw_text(painter, left, y, row[1], theme.HEADER)
                y += HEADER_GAP
            elif kind == "root":
                draw_text(painter, left + INDENT_ROOT * cell, y, self._fit(row[1], INDENT_ROOT), theme.TEXT)
            elif kind == "feed":
                self._paint_feed(painter, left, y, row[1])
            elif kind == "info":
                draw_text(painter, left + INDENT_ROOT * cell, y, row[1], theme.LABEL)
                draw_text(painter, left + INDENT_VALUE * cell, y, self._fit(row[2], INDENT_VALUE), theme.TEXT)
            y += theme.CELL_H
        self._paint_link(painter)
        draw_text(painter, theme.PAD_X, self.height() - FOOTER_MARGIN - theme.CELL_H, VERSION_TEXT, theme.HEADER)
        if self.overlay:
            painter.fillRect(self.width() - 1, 0, 1, self.height(), theme.qcolor(theme.BORDER))

    def _paint_link(self, painter):
        rect = self._link_rect()
        color = theme.qcolor(theme.TEXT if self.hover else theme.HEADER)
        draw_text(painter, int(rect.x()), int(rect.y()), LINK_TEXT, theme.TEXT if self.hover else theme.HEADER)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(rect.x() + len(LINK_TEXT) * theme.CELL_W + LINK_SPACE, rect.y() + (theme.CELL_H - LINK_ICON) / 2 + LINK_DROP)
        painter.scale(LINK_ICON / 24, LINK_ICON / 24)
        pen = QPen(color, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
        painter.drawLine(QPointF(9, 15), QPointF(16, 8))
        painter.drawPolyline([QPointF(10, 8), QPointF(16, 8), QPointF(16, 14)])
        painter.restore()

    def _paint_feed(self, painter, left, y, name):
        state = self.states.get(name, OFF_STATE)
        color = {OFF_STATE: theme.OFF, READY_STATE: theme.GREEN, FAILED_STATE: theme.RED}[state]
        center = QPointF(left + INDENT_DOT * theme.CELL_W + theme.CELL_W / 2, y + theme.CELL_H / 2)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if state == OFF_STATE:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.qcolor(theme.HEADER), 1))
            painter.drawEllipse(center, DOT_RADIUS - 0.5, DOT_RADIUS - 0.5)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(color))
            painter.drawEllipse(center, DOT_RADIUS + 0.4, DOT_RADIUS + 0.4)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        draw_text(painter, left + INDENT_NAME * theme.CELL_W, y, self._fit(name, INDENT_NAME), theme.OFF)
