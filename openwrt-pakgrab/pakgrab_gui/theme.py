from functools import lru_cache

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF

CELL_W = 8
CELL_H = 16
PAD_X = 16
PAD_TOP = 16
PAD_BOTTOM = 8
SIDEBAR_COLS = 36
STATUS_HEIGHT = 24
SCROLL_WIDTH = 16

BG_MAIN = 0x2D2A2E
BG_SIDE = 0x221F22
BG_BAR = 0x19181A
BG_BAND = 0x363337
BG_SELECT = 0x4A474C
BG_ITEM = 0x403E41
BORDER = 0x403E41

TEXT = 0xFCFCFA
DIM = 0x6F6D70
LABEL = 0x8B898C
HEADER = 0x727072
OFF = 0x7C7A7D
GREEN = 0xA9DC76
GREEN_SOFT = 0x9DBF8C
ORANGE = 0xFD971F
RED = 0xFF6188
YELLOW = 0xFFD866
BLUE = 0x66AAFF
RULE = 0xC4C3C5
CURSOR = 0xCCD5CC

FAMILIES = (
    "Cascadia Mono",
    "Cascadia Code",
    "JetBrains Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Menlo",
    "Liberation Mono",
    "Courier New",
)


@lru_cache(maxsize=None)
def qcolor(rgb):
    return QColor((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)


def sgr(rgb):
    return "\033[38;2;%d;%d;%dm" % ((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)


RESET = "\033[0m"


@lru_cache(maxsize=None)
def fonts():
    available = set(QFontDatabase.families())
    family = next((name for name in FAMILIES if name in available), None)
    if family:
        font = QFont(family)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    probe = QFont(font)
    probe.setPixelSize(100)
    advance = QFontMetricsF(probe).horizontalAdvance("M")
    font.setPixelSize(max(11, min(16, round(CELL_W * 100 / advance))))
    metrics = QFontMetricsF(font)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, CELL_W - metrics.horizontalAdvance("M"))
    bold = QFont(font)
    bold.setBold(True)
    baseline = (CELL_H - (metrics.ascent() + metrics.descent())) / 2 + metrics.ascent()
    return font, bold, round(baseline)
