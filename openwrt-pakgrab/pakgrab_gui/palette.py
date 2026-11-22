from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import theme

STYLE = """
#palette { background: #221f22; border: 1px solid #403e41; }
#palette QLineEdit { background: #2d2a2e; color: #fcfcfa; border: 0; padding: 7px 10px; selection-background-color: #4a474c; }
#palette QListWidget { background: #221f22; color: #fcfcfa; border: 0; outline: 0; }
#palette QListWidget::item { padding: 0; }
#palette QListWidget::item:selected { background: #403e41; }
#palette QLabel { background: transparent; }
"""
WIDTH_COLS = 64
MAX_ROWS = 10
ROW_HEIGHT = 24


class PaletteEdit(QLineEdit):
    move = Signal(int)
    cancelled = Signal()
    accepted = Signal()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Down:
            self.move.emit(1)
        elif key == Qt.Key.Key_Up:
            self.move.emit(-1)
        elif key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accepted.emit()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.cancelled.emit()


class Palette(QFrame):
    chosen = Signal(str, bool)
    dismissed = Signal()

    def __init__(self, items, parent):
        super().__init__(parent)
        self.setObjectName("palette")
        self.setStyleSheet(STYLE)
        self.items = list(items)
        self.setFixedWidth(WIDTH_COLS * theme.CELL_W)
        self.edit = PaletteEdit()
        self.edit.setPlaceholderText("Search for commands...")
        self.listing = QListWidget()
        self.listing.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.listing.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.edit)
        layout.addWidget(self.listing)
        normal = theme.fonts()[0]
        self.edit.setFont(normal)
        self.listing.setFont(normal)
        self.edit.textChanged.connect(self._filter)
        self.edit.move.connect(self._move)
        self.edit.cancelled.connect(self.close_palette)
        self.edit.accepted.connect(self._accept)
        self.listing.itemClicked.connect(lambda _item: self._accept())
        self.hide()

    def open_palette(self):
        self.edit.clear()
        self._filter("")
        self.reposition()
        self.show()
        self.raise_()
        self.edit.setFocus()

    def close_palette(self):
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    def reposition(self):
        parent = self.parentWidget()
        self.move(max(0, (parent.width() - self.width()) // 2), theme.PAD_TOP)

    def _filter(self, text):
        needle = text.strip().lower()
        self.listing.clear()
        matches = [item for item in self.items if needle in (item[0] + " " + item[1]).lower()]
        for entry in matches[:MAX_ROWS]:
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, entry)
            label = QLabel(
                '<span style="color:#fcfcfa">%s</span>&nbsp;&nbsp;<span style="color:#727072">%s</span>'
                % (entry[0], entry[1])
            )
            label.setFont(theme.fonts()[0])
            label.setContentsMargins(10, 0, 10, 0)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.setSizeHint(QSize(0, ROW_HEIGHT))
            self.listing.addItem(row)
            self.listing.setItemWidget(row, label)
        if matches:
            self.listing.setCurrentRow(0)
        self.listing.setFixedHeight(max(1, min(len(matches), MAX_ROWS)) * ROW_HEIGHT + 4)
        self.adjustSize()

    def _move(self, step):
        count = self.listing.count()
        if count:
            self.listing.setCurrentRow((self.listing.currentRow() + step) % count)

    def _accept(self):
        item = self.listing.currentItem()
        if item is None:
            return
        label, _description, text, run = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        self.chosen.emit(text, run)
