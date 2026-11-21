import bisect
import os
from itertools import islice

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import QAbstractScrollArea, QFrame

from . import theme
from .ansi import build, parse
from .paint import draw_text

MAX_LINES = 20000
TRIM_LINES = 2000
MAX_INPUT = 4096

SCROLL_STYLE = """
QScrollBar:vertical { background: #19181a; width: 16px; margin: 0; border: 0; }
QScrollBar::handle:vertical { background: #3b393d; min-height: 24px; margin: 0; border: 0; }
QScrollBar::handle:vertical:hover { background: #4a484c; }
QScrollBar::handle:vertical:disabled { background: #19181a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; width: 0; border: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #19181a; }
"""


class TerminalView(QAbstractScrollArea):
    submitted = Signal(str)
    answered = Signal(str)
    interrupted = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(SCROLL_STYLE)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.lines = []
        self.starts = []
        self.total = 0
        self.count = 0
        self.live = []
        self.tail = []
        self.cols = 80
        self.visible = 24
        self.follow = True
        self.prompt = ()
        self.mode = None
        self.buffer = ""
        self.cursor = 0
        self.history = []
        self.hist_pos = None
        self.draft = ""
        self.completer = None
        self.cursor_on = True
        self.anchor = None
        self.head = None
        self.dragging = False
        self.input_line = None
        self.blink = QTimer(self)
        self.blink.setInterval(530)
        self.blink.timeout.connect(self._toggle)
        bar = self.verticalScrollBar()
        bar.setSingleStep(1)
        bar.valueChanged.connect(self._scrolled)

    def focusNextPrevChild(self, forward):
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        viewport = self.viewport()
        cols = max(20, (viewport.width() - 2 * theme.PAD_X) // theme.CELL_W)
        self.visible = max(1, (viewport.height() - theme.PAD_TOP - theme.PAD_BOTTOM) // theme.CELL_H)
        if cols != self.cols:
            self.cols = cols
            self._reflow()
        self._sync()

    def _reflow(self):
        total = 0
        starts = []
        for line in self.lines:
            starts.append(total)
            total += line.height(self.cols)
        self.starts = starts
        self.total = total

    def _push(self, line):
        self.starts.append(self.total)
        self.total += line.height(self.cols)
        self.lines.append(line)

    def _trim(self):
        dropped = self.lines[:TRIM_LINES]
        removed = sum(line.height(self.cols) for line in dropped)
        del self.lines[:TRIM_LINES]
        self._reflow()
        self.anchor = self.head = None
        bar = self.verticalScrollBar()
        if not self.follow:
            bar.setValue(max(0, bar.value() - removed))

    def _sync(self):
        self.count = self.total + sum(line.height(self.cols) for line in self.tail)
        bar = self.verticalScrollBar()
        maximum = max(0, self.count - self.visible)
        bar.setRange(0, maximum)
        bar.setPageStep(max(1, self.visible - 1))
        bar.setEnabled(maximum > 0)
        if self.follow:
            bar.setValue(maximum)
        self.viewport().update()

    def _scrolled(self, value):
        self.follow = value >= self.verticalScrollBar().maximum()
        self.viewport().update()

    def _rebuild_tail(self):
        tail = list(self.live)
        if self.mode:
            if self.input_line is None:
                self.input_line = build(self._segments(" "), band=True)
            tail.append(self.input_line)
        self.tail = tail

    def _segments(self, pad=""):
        return list(self.prompt) + [(self.buffer + pad, None, False)]

    def _touch(self):
        self.input_line = None
        self._rebuild_tail()
        self._sync()

    def append(self, texts, clip=False):
        for text in texts:
            self._push(parse(text, clip=clip))
        if len(self.lines) > MAX_LINES:
            self._trim()
        self._sync()

    def set_live(self, texts):
        self.live = [parse(text, clip=True) for text in texts] if texts else []
        self._rebuild_tail()
        self._sync()

    def clear(self):
        self.lines = []
        self.starts = []
        self.total = 0
        self.anchor = self.head = None
        self.follow = True
        self._rebuild_tail()
        self._sync()

    def begin_input(self, prompt, mode):
        self.prompt = tuple(prompt)
        self.mode = mode
        self.buffer = ""
        self.cursor = 0
        self.hist_pos = None
        self.follow = True
        self.cursor_on = True
        self._touch()
        self._restart_blink()

    def end_input(self):
        self.mode = None
        self.buffer = ""
        self.cursor = 0
        self.blink.stop()
        self._touch()

    def insert_text(self, text):
        if not self.mode:
            return
        text = "".join(char for char in text if char.isprintable())
        if not text:
            return
        room = MAX_INPUT - len(self.buffer)
        text = text[:max(0, room)]
        self.buffer = self.buffer[:self.cursor] + text + self.buffer[self.cursor:]
        self.cursor += len(text)
        self._edited()

    def set_text(self, text):
        if self.mode:
            self.buffer = text[:MAX_INPUT]
            self.cursor = len(self.buffer)
            self._edited()

    def submit_text(self, text):
        if self.mode:
            self.set_text(text)
            self._submit()

    def _edited(self):
        self.anchor = self.head = None
        self.follow = True
        self.cursor_on = True
        self._touch()
        self._restart_blink()

    def _restart_blink(self):
        if self.mode and self.hasFocus():
            self.cursor_on = True
            self.blink.start()
        else:
            self.blink.stop()

    def _toggle(self):
        self.cursor_on = not self.cursor_on
        self.viewport().update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._restart_blink()
        self.viewport().update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.blink.stop()
        self.viewport().update()

    def _echo(self, suffix=""):
        segments = self._segments(suffix)
        self._push(build(segments))
        self.anchor = self.head = None

    def _leave_input(self, suffix=""):
        self._echo(suffix)
        self.mode = None
        self.buffer = ""
        self.cursor = 0
        self.blink.stop()
        self.follow = True
        self._touch()

    def _submit(self):
        text = self.buffer
        mode = self.mode
        self._leave_input()
        if mode == "shell":
            if text.strip() and (not self.history or self.history[-1] != text):
                self.history.append(text)
            self.submitted.emit(text)
        else:
            self.answered.emit(text)

    def _cancel_line(self):
        self._echo("^C")
        self.buffer = ""
        self.cursor = 0
        self._edited()

    def _history_step(self, step):
        if not self.history:
            return
        if self.hist_pos is None:
            if step > 0:
                return
            self.draft = self.buffer
            self.hist_pos = len(self.history)
        position = self.hist_pos + step
        if position < 0:
            return
        if position >= len(self.history):
            self.hist_pos = None
            self.set_text(self.draft)
            return
        self.hist_pos = position
        self.set_text(self.history[position])

    def _complete(self):
        if self.completer is None:
            return
        head = self.buffer[:self.cursor]
        tokens = head.split(" ")
        word = tokens[-1]
        options = [option for option in self.completer(tokens[:-1]) if option.startswith(word)]
        if not options:
            return
        common = os.path.commonprefix(options)
        if len(options) == 1:
            common += " "
        if len(common) > len(word):
            tail = self.buffer[self.cursor:]
            self.buffer = head[:len(head) - len(word)] + common + tail
            self.cursor = len(head) - len(word) + len(common)
            self._edited()
        elif len(options) > 1:
            self._echo()
            self._push(parse("  ".join(options)))
            self._touch()

    def _word_left(self):
        position = self.cursor
        while position > 0 and self.buffer[position - 1] == " ":
            position -= 1
        while position > 0 and self.buffer[position - 1] != " ":
            position -= 1
        return position

    def _word_right(self):
        position = self.cursor
        size = len(self.buffer)
        while position < size and self.buffer[position] == " ":
            position += 1
        while position < size and self.buffer[position] != " ":
            position += 1
        return position

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        bar = self.verticalScrollBar()
        if ctrl and key == Qt.Key.Key_C:
            if shift or self.has_selection():
                self.copy_selection()
            elif self.mode == "shell":
                self._cancel_line()
            elif self.mode == "ask":
                self._leave_input("^C")
                self.answered.emit("")
            else:
                self.interrupted.emit()
            return
        if key == Qt.Key.Key_PageUp:
            bar.setValue(bar.value() - bar.pageStep())
            return
        if key == Qt.Key.Key_PageDown:
            bar.setValue(bar.value() + bar.pageStep())
            return
        if not self.mode:
            return
        if (ctrl and key == Qt.Key.Key_V) or (shift and key == Qt.Key.Key_Insert):
            self.paste()
        elif ctrl and key == Qt.Key.Key_L:
            self.clear()
        elif ctrl and key == Qt.Key.Key_D:
            if not self.buffer and self.mode == "shell":
                self.closed.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
        elif key == Qt.Key.Key_Backspace:
            if self.cursor:
                self.buffer = self.buffer[:self.cursor - 1] + self.buffer[self.cursor:]
                self.cursor -= 1
                self._edited()
        elif key == Qt.Key.Key_Delete:
            self.buffer = self.buffer[:self.cursor] + self.buffer[self.cursor + 1:]
            self._edited()
        elif key == Qt.Key.Key_Left:
            self.cursor = self._word_left() if ctrl else max(0, self.cursor - 1)
            self._edited()
        elif key == Qt.Key.Key_Right:
            self.cursor = self._word_right() if ctrl else min(len(self.buffer), self.cursor + 1)
            self._edited()
        elif key == Qt.Key.Key_Home or (ctrl and key == Qt.Key.Key_A):
            self.cursor = 0
            self._edited()
        elif key == Qt.Key.Key_End or (ctrl and key == Qt.Key.Key_E):
            self.cursor = len(self.buffer)
            self._edited()
        elif ctrl and key == Qt.Key.Key_U:
            self.buffer = self.buffer[self.cursor:]
            self.cursor = 0
            self._edited()
        elif ctrl and key == Qt.Key.Key_K:
            self.buffer = self.buffer[:self.cursor]
            self._edited()
        elif ctrl and key == Qt.Key.Key_W:
            start = self._word_left()
            self.buffer = self.buffer[:start] + self.buffer[self.cursor:]
            self.cursor = start
            self._edited()
        elif key == Qt.Key.Key_Up and self.mode == "shell":
            self._history_step(-1)
        elif key == Qt.Key.Key_Down and self.mode == "shell":
            self._history_step(1)
        elif key == Qt.Key.Key_Tab and self.mode == "shell":
            self._complete()
        else:
            text = event.text()
            if text and text.isprintable() and not (ctrl and not alt):
                self.insert_text(text)

    def paste(self):
        text = QGuiApplication.clipboard().text()
        self.insert_text(" ".join(text.split()))

    def has_selection(self):
        return self.anchor is not None and self.head is not None and self.anchor != self.head

    def _selection(self):
        if not self.has_selection():
            return None
        return min(self.anchor, self.head), max(self.anchor, self.head)

    def _row(self, index):
        return next(islice(self._iter_rows(index), 1), None)

    def selected_text(self):
        selection = self._selection()
        if selection is None:
            return ""
        (first_row, first_col), (last_row, last_col) = selection
        pieces = []
        for row in range(first_row, last_row + 1):
            found = self._row(row)
            if found is None:
                break
            line, sub = found
            start = sub * self.cols
            begin = start + (first_col if row == first_row else 0)
            end = start + (last_col if row == last_row else self.cols)
            piece = line.plain[begin:end]
            if row != last_row:
                piece = piece.rstrip()
            if pieces and sub == 0:
                pieces.append("\n")
            pieces.append(piece)
        return "".join(pieces).rstrip()

    def copy_selection(self):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
        self.anchor = self.head = None
        self.viewport().update()

    def _cell(self, position):
        row = self.verticalScrollBar().value() + (position.y() - theme.PAD_TOP) // theme.CELL_H
        column = (position.x() - theme.PAD_X + theme.CELL_W // 2) // theme.CELL_W
        return max(0, min(self.count - 1, row)), max(0, min(self.cols, column))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.anchor = self.head = self._cell(event.position().toPoint())
            self.dragging = True
            self.viewport().update()

    def mouseMoveEvent(self, event):
        if not self.dragging:
            return
        position = event.position().toPoint()
        bar = self.verticalScrollBar()
        if position.y() < theme.PAD_TOP:
            bar.setValue(bar.value() - 1)
        elif position.y() > self.viewport().height() - theme.PAD_BOTTOM:
            bar.setValue(bar.value() + 1)
        self.head = self._cell(position)
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            if not self.has_selection():
                self.anchor = self.head = None
            self.viewport().update()

    def contextMenuEvent(self, event):
        if self.has_selection():
            self.copy_selection()
        elif self.mode:
            self.paste()

    def _iter_rows(self, first):
        index = bisect.bisect_right(self.starts, first) - 1
        skip = 0
        if self.lines and 0 <= first < self.total and index >= 0:
            sub = first - self.starts[index]
            while index < len(self.lines):
                line = self.lines[index]
                for part in range(sub, line.height(self.cols)):
                    yield line, part
                index += 1
                sub = 0
        else:
            skip = max(0, first - self.total)
        for line in self.tail:
            height = line.height(self.cols)
            if skip >= height:
                skip -= height
                continue
            for part in range(skip, height):
                yield line, part
            skip = 0

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        width = self.viewport().width()
        painter.fillRect(event.rect(), theme.qcolor(theme.BG_MAIN))
        first = self.verticalScrollBar().value()
        rows = list(islice(self._iter_rows(first), self.visible))
        selection = self._selection()
        for position, (line, sub) in enumerate(rows):
            y = theme.PAD_TOP + position * theme.CELL_H
            if line.band:
                painter.fillRect(0, y, width, theme.CELL_H, theme.qcolor(theme.BG_BAND))
            if selection is not None:
                self._paint_selection(painter, selection, first + position, y)
        for position, (line, sub) in enumerate(rows):
            y = theme.PAD_TOP + position * theme.CELL_H
            start = sub * self.cols
            end = start + self.cols
            for run_start, run_end, fg, bold in line.runs:
                if run_end <= start:
                    continue
                if run_start >= end:
                    break
                a = max(run_start, start)
                b = min(run_end, end)
                color = theme.TEXT if fg is None else fg
                draw_text(painter, theme.PAD_X + (a - start) * theme.CELL_W, y, line.plain[a:b], color, bold)
        if self.mode and self.cursor_on and self.hasFocus():
            self._paint_cursor(painter, first)

    def _paint_selection(self, painter, selection, row, y):
        (first_row, first_col), (last_row, last_col) = selection
        if row < first_row or row > last_row:
            return
        begin = first_col if row == first_row else 0
        end = last_col if row == last_row else self.cols
        if end > begin:
            painter.fillRect(
                theme.PAD_X + begin * theme.CELL_W,
                y,
                (end - begin) * theme.CELL_W,
                theme.CELL_H,
                theme.qcolor(theme.BG_SELECT),
            )

    def _paint_cursor(self, painter, first):
        line = self.tail[-1]
        index = sum(len(text) for text, _fg, _bold in self.prompt) + self.cursor
        row = self.count - line.height(self.cols) + index // self.cols
        position = row - first
        if 0 <= position < self.visible:
            x = theme.PAD_X + (index % self.cols) * theme.CELL_W
            y = theme.PAD_TOP + position * theme.CELL_H + theme.CELL_H - 3
            painter.fillRect(QRect(x, y, theme.CELL_W, 2), theme.qcolor(theme.CURSOR))
