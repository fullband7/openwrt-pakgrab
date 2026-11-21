import re

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
ESC_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

STANDARD = {
    30: 0x19181A,
    31: 0xFF6188,
    32: 0xA9DC76,
    33: 0xFFD866,
    34: 0x66AAFF,
    35: 0xAB9DF2,
    36: 0x78DCE8,
    37: 0xFCFCFA,
    90: 0x727072,
    91: 0xFF6188,
    92: 0xA9DC76,
    93: 0xFFD866,
    94: 0x66AAFF,
    95: 0xAB9DF2,
    96: 0x78DCE8,
    97: 0xFCFCFA,
}


class Line:
    __slots__ = ("plain", "runs", "band", "clip", "rows", "cols")

    def __init__(self, plain, runs, band=False, clip=False):
        self.plain = plain
        self.runs = runs
        self.band = band
        self.clip = clip
        self.rows = 1
        self.cols = 0

    def height(self, cols):
        if self.clip:
            return 1
        if self.cols != cols:
            self.cols = cols
            self.rows = max(1, -(-len(self.plain) // cols))
        return self.rows


def _apply(code, fg, bold):
    values = [int(item) if item else 0 for item in code.split(";")]
    index = 0
    while index < len(values):
        value = values[index]
        if value == 0:
            fg, bold = None, False
        elif value == 1:
            bold = True
        elif value == 22:
            bold = False
        elif value == 39:
            fg = None
        elif value in STANDARD:
            fg = STANDARD[value]
        elif value == 38 and index + 1 < len(values):
            if values[index + 1] == 2 and index + 4 < len(values):
                fg = (values[index + 2] << 16) | (values[index + 3] << 8) | values[index + 4]
                index += 4
            elif values[index + 1] == 5:
                index += 2
        index += 1
    return fg, bold


def _clean(text):
    text = ESC_RE.sub("", text)
    if text.isprintable():
        return text
    return "".join(char if char.isprintable() else "?" for char in text)


def parse(text, band=False, clip=False):
    segments = []
    fg = None
    bold = False
    for position, part in enumerate(SGR_RE.split(text.replace("\t", "    "))):
        if position % 2:
            fg, bold = _apply(part, fg, bold)
        else:
            part = _clean(part)
            if part:
                segments.append((part, fg, bold))
    return build(segments, band, clip)


def build(segments, band=False, clip=False):
    plain = []
    runs = []
    length = 0
    for text, fg, bold in segments:
        if not text:
            continue
        end = length + len(text)
        if runs and runs[-1][1] == length and runs[-1][2] == fg and runs[-1][3] == bold:
            runs[-1] = (runs[-1][0], end, fg, bold)
        else:
            runs.append((length, end, fg, bold))
        plain.append(text)
        length = end
    return Line("".join(plain), tuple(runs), band, clip)
