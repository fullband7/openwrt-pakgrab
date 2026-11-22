from PySide6.QtCore import QPointF, QRectF, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QPainter, QPen
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
FILE_HEIGHT = 24


class SideBar(QWidget):
    selected = Signal(str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self.files = []
        self.active = ""
        self.feeds = []
        self.session = []
        self.states = {}
        self.offset = 0
        self.overlay = False
        self.hover = False
        self.hover_file = None
        self.locked = False
        self.setMouseTracking(True)
        self.setFixedWidth(theme.SIDEBAR_COLS * theme.CELL_W)
        self.setMinimumHeight(theme.PAD_TOP + 10 * theme.CELL_H + self._footer())

    @staticmethod
    def _footer():
        return theme.CELL_H * 2 + VERSION_GAP + FOOTER_MARGIN * 2

    def load(self, files, active, feeds, session):
        self.files = list(files)
        self.active = active
        self.feeds = list(feeds)
        self.session = list(session)
        self.states = {}
        self.offset = max(0, min(self.offset, len(self._rows()) - self._visible()))
        self._track()
        self.update()

    def set_locked(self, locked):
        if locked != self.locked:
            self.locked = locked
            self._track()
            self.update()

    def set_feed(self, name, ok):
        self.states[name] = READY_STATE if ok else FAILED_STATE
        self.update()

    def _rows(self):
        rows = [("header", "FEEDS")]
        rows.extend(("file", name) for name in self.files)
        rows.append(("blank",))
        rows.extend((("header", "FOLDERS"), ("root", self.root)))
        rows.extend(("feed", name) for name in self.feeds)
        rows.append(("blank",))
        rows.append(("header", "SESSION"))
        rows.extend(("info", label, value) for label, value in self.session)
        return rows

    def _limit(self):
        return self.height() - self._footer()

    def _visible(self):
        return max(1, (self._limit() - theme.PAD_TOP) // theme.CELL_H)

    def _placed(self):
        y = theme.PAD_TOP
        limit = self._limit()
        for row in self._rows()[self.offset:]:
            kind = row[0]
            height = FILE_HEIGHT if kind == "file" else theme.CELL_H
            if y + height > limit:
                return
            yield row, y
            y += height + (HEADER_GAP if kind == "header" else 0)

    def _file_at(self, position):
        if self.locked:
            return None
        for row, y in self._placed():
            if row[0] == "file" and y <= position.y() < y + FILE_HEIGHT:
                return row[1]
        return None

    def _track(self, position=None):
        if position is None:
            position = QPointF(self.mapFromGlobal(QCursor.pos()))
            if not self.rect().contains(position.toPoint()):
                position = QPointF(-1, -1)
        file = self._file_at(position)
        inside = self._link_rect().contains(position)
        if file != self.hover_file or inside != self.hover:
            self.hover_file = file
            self.hover = inside
            self.setCursor(Qt.CursorShape.PointingHandCursor if inside or file else Qt.CursorShape.ArrowCursor)
            self.update()

    def wheelEvent(self, event):
        rows = len(self._rows())
        step = -1 if event.angleDelta().y() > 0 else 1
        self.offset = max(0, min(max(0, rows - self._visible()), self.offset + step))
        self._track()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.offset = max(0, min(self.offset, len(self._rows()) - self._visible()))
        self._track()

    def _fit(self, text, indent):
        room = self.width() // theme.CELL_W - theme.PAD_X // theme.CELL_W - indent - 1
        return text if len(text) <= room else text[:room - 1] + "…"

    def _link_rect(self):
        width = len(LINK_TEXT) * theme.CELL_W + LINK_SPACE + LINK_ICON
        return QRectF(theme.PAD_X, self.height() - FOOTER_MARGIN - theme.CELL_H * 2 - VERSION_GAP, width, theme.CELL_H)

    def mouseMoveEvent(self, event):
        self._track(event.position())

    def leaveEvent(self, event):
        self._track(QPointF(-1, -1))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._link_rect().contains(event.position()):
            QDesktopServices.openUrl(QUrl(LINK_URL))
            return
        name = self._file_at(event.position())
        if name is not None and name != self.active:
            self.selected.emit(name)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor(theme.BG_SIDE))
        cell = theme.CELL_W
        left = theme.PAD_X
        limit = self._limit()
        for row, y in self._placed():
            kind = row[0]
            if kind == "header":
                draw_text(painter, left, y, row[1], theme.HEADER)
            elif kind == "file":
                self._paint_file(painter, y, row[1])
            elif kind == "root":
                draw_text(painter, left + INDENT_ROOT * cell, y, self._fit(row[1], INDENT_ROOT), theme.TEXT)
            elif kind == "feed":
                self._paint_feed(painter, left, y, row[1])
            elif kind == "info":
                draw_text(painter, left + INDENT_ROOT * cell, y, row[1], theme.LABEL)
                draw_text(painter, left + INDENT_VALUE * cell, y, self._fit(row[2], INDENT_VALUE), theme.TEXT)
        self._paint_link(painter)
        draw_text(painter, theme.PAD_X, self.height() - FOOTER_MARGIN - theme.CELL_H, VERSION_TEXT, theme.HEADER)
        if self.overlay:
            painter.fillRect(self.width() - 1, 0, 1, self.height(), theme.qcolor(theme.BORDER))

    def _paint_file(self, painter, y, name):
        active = name == self.active
        hovered = name == self.hover_file
        if hovered:
            painter.fillRect(0, y, self.width(), FILE_HEIGHT, theme.qcolor(theme.BG_ITEM))
        if self.locked:
            color = theme.DIM
        elif active or hovered:
            color = theme.TEXT
        else:
            color = theme.LABEL
        draw_text(painter, theme.PAD_X + INDENT_ROOT * theme.CELL_W, y + (FILE_HEIGHT - theme.CELL_H) // 2, self._fit(name, INDENT_ROOT), color, active)

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
