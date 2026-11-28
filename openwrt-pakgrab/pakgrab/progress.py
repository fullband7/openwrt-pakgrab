import threading
import time
from collections import deque
from dataclasses import dataclass

BAR_WIDTH = 30
REFRESH_INTERVAL = 0.08
SPINNER_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_ASCII = "|/-\\"
SPEED_WINDOW = 2.0
BAR_PARTIAL = " ▏▎▍▌▋▊▉"
BAR_FULL = "█"
BAR_EMPTY = "░"
ARROW_UNICODE = "↓"
ARROW_ASCII = ">"
SPINNER_COLOR = "97"
ARROW_COLOR = "38;2;255;136;0"
BAR_COLOR = "38;2;0;179;89"
TRACK_COLOR = "90"
TITLE_COLOR = "1"
SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_size(value):
    value = float(max(0, value))
    index = 0
    while value >= 1024 and index < len(SIZE_UNITS) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return "%d B" % value
    return ("%.1f %s" if value < 10 else "%.0f %s") % (value, SIZE_UNITS[index])


def format_rate(value):
    return format_size(value) + "/s"


def format_duration(seconds):
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%02d:%02d" % (minutes, seconds)


class Entry:
    __slots__ = ("name", "size", "done")

    def __init__(self, name, size):
        self.name = name
        self.size = size
        self.done = 0


@dataclass(frozen=True)
class Status:
    done: int
    total: int
    size_done: int
    size_total: int


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.local = threading.local()
        self.entries = {}
        self.wire = 0

    def enter(self, name, size=0):
        entry = Entry(name, size)
        self.local.entry = entry
        with self.lock:
            self.entries[threading.get_ident()] = entry

    def leave(self):
        self.local.entry = None
        with self.lock:
            self.entries.pop(threading.get_ident(), None)

    def seed(self, offset):
        entry = getattr(self.local, "entry", None)
        if entry is not None:
            with self.lock:
                entry.done = offset

    def advance(self, amount):
        entry = getattr(self.local, "entry", None)
        with self.lock:
            self.wire += amount
            if entry is not None:
                entry.done += amount

    def snapshot(self):
        with self.lock:
            return self.wire, [(entry.name, entry.done, entry.size) for entry in self.entries.values()]


class Board:
    def __init__(self, console, tracker):
        self.console = console
        self.tracker = tracker
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def speed(self, now, wire):
        self.samples.append((now, wire))
        while len(self.samples) > 2 and now - self.samples[0][0] > SPEED_WINDOW:
            self.samples.popleft()
        then, before = self.samples[0]
        return (wire - before) / (now - then) if now > then else 0.0

    def bar(self, done, total):
        icons = self.console.icons
        total = max(1, total)
        done = min(done, total)
        if icons != "unicode":
            filled = done * BAR_WIDTH // total
            full_char, empty_char = (BAR_FULL, BAR_EMPTY) if icons == "basic" else ("#", "-")
            return self.console.paint(full_char * filled, BAR_COLOR) + self.console.paint(empty_char * (BAR_WIDTH - filled), TRACK_COLOR)
        full, part = divmod(done * BAR_WIDTH * 8 // total, 8)
        fill = BAR_FULL * full + (BAR_PARTIAL[part] if part else "")
        return self.console.paint(fill, BAR_COLOR) + self.console.paint(BAR_EMPTY * (BAR_WIDTH - len(fill)), TRACK_COLOR)

    def frame(self, title, status, started, slots):
        now = time.monotonic()
        elapsed = now - started
        wire, active = self.tracker.snapshot()
        speed = self.speed(now, wire)
        icons = self.console.icons
        spinner = SPINNER_UNICODE if icons == "unicode" else SPINNER_ASCII
        tick = spinner[int(elapsed / REFRESH_INTERVAL) % len(spinner)]
        flight = sum(min(done, size) if size else done for _name, done, size in active)
        if status.size_total:
            numerator, denominator = min(status.size_done + flight, status.size_total), status.size_total
        else:
            numerator, denominator = status.done, max(1, status.total)
        remaining = status.size_total - numerator if status.size_total else 0
        eta = remaining / speed if speed > 0 and remaining > 0 else (0 if not remaining else None)
        parts = [
            "%s %s %3d%%" % (self.console.paint(tick, SPINNER_COLOR), self.bar(numerator, denominator), numerator * 100 // denominator),
            format_rate(speed),
            "%d/%d active" % (len(active), slots),
            "%d/%d" % (status.done, status.total),
        ]
        if status.size_total:
            parts.append("%s/%s" % (format_size(numerator), format_size(status.size_total)))
            parts.append("ETA " + format_duration(eta))
        parts.append(format_duration(elapsed))
        arrow = self.console.paint(ARROW_UNICODE if icons != "ascii" else ARROW_ASCII, ARROW_COLOR)
        rows = []
        for name, done, size in active[:slots]:
            percent = "%3d%%" % (min(done, size) * 100 // size) if size else "    "
            rows.append("  %s %s  %s" % (arrow, percent, name))
        rows += [""] * (slots - len(rows))
        return [self.console.paint(title, TITLE_COLOR), "  ".join(parts)] + rows
