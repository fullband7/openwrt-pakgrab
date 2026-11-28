import re
import threading
import time

from .settings import SettingsError

MIN_RATE = 1024
MIN_CHUNK = 1024
UNITS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
RATE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmg]?)(?:i?b)?(?:/s)?$")
UNLIMITED = ("0", "off", "none", "unlimited")


def parse_rate(text, default_unit=""):
    value = str(text).strip().lower()
    if value in UNLIMITED:
        return 0
    match = RATE_RE.match(value)
    if not match:
        raise SettingsError("invalid rate '%s', use for example 500K or 2M" % str(text)[:32])
    number, unit = match.groups()
    rate = int(float(number) * UNITS[unit or default_unit])
    if rate == 0:
        return 0
    if rate < MIN_RATE:
        raise SettingsError("rate must be at least 1K")
    return rate


def parse_kib_rate(text):
    return parse_rate(text, "k")


def chunk_size(rate, ceiling):
    if rate <= 0:
        return ceiling
    return max(MIN_CHUNK, min(ceiling, rate // 4))


class RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = 0.0

    def acquire(self, size, rate, cancel):
        if rate <= 0 or size <= 0:
            return
        with self.lock:
            now = time.monotonic()
            start = max(self.ready, now)
            self.ready = start + size / rate
        delay = start - now
        if delay > 0:
            cancel.wait(delay)
