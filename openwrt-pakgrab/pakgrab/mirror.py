import queue
import threading
import time
from collections import deque

from .fs import safe_join, write_atomic
from .index import compare_versions
from .net import FetchError
from .versions import CURRENT, DOWNLOADED, UPDATED

MAX_CONCURRENT = 5
MAX_CONCURRENT_LIMIT = 16
BAR_WIDTH = 30
REFRESH_INTERVAL = 0.08
SPINNER_UNICODE = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
SPINNER_ASCII = "|/-\\"
SPEED_WINDOW = 2.0
BAR_PARTIAL = " \u258f\u258e\u258d\u258c\u258b\u258a\u2589"
BAR_FULL = "\u2588"
BAR_EMPTY = "\u2591"
ARROW_UNICODE = "\u2193"
ARROW_ASCII = ">"
SPINNER_COLOR = "97"
ARROW_COLOR = "38;2;255;136;0"
BAR_COLOR = "38;2;0;179;89"
TRACK_COLOR = "90"
TITLE_COLOR = "1"
SPEED_UNITS = ((1e9, "Gb/s"), (1e6, "Mb/s"), (0, "Kb/s"))
REQUEST_DELAY = 0.1
RETRIES = 3
RETRY_BACKOFF = 2.0
RATE_LIMIT_STATUSES = (429, 503)
MAX_ROOT_FILE_SIZE = 4 * 1024 * 1024
ROOT_FILES = ("sha256sums", "sha256sums.asc", "sha256sums.sig")


def _title(segments):
    segments = tuple(segments)
    if "releases" in segments:
        segments = segments[segments.index("releases") + 2:]
    elif "snapshots" in segments:
        segments = segments[segments.index("snapshots") + 1:]
    return "/".join(segments) or "."


class Mirror:
    def __init__(self, manager):
        self.manager = manager
        self.console = manager.console
        self.workers = max(1, min(MAX_CONCURRENT, MAX_CONCURRENT_LIMIT))
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.resume = 0.0
        self.errors = []
        self.active = []
        self.done = 0
        self.counts = {}
        self.failed = 0
        self.samples = deque()

    def run(self):
        index = self.manager._ready_index()
        if index is None:
            return 1
        try:
            for title, tasks, quiet in self._units(index):
                self._run_unit(title, tasks, quiet)
        except BaseException:
            self.stop.set()
            raise
        finally:
            self.console.live_clear()
            self.manager.fetcher.close()
        return self._summary()

    def _units(self, index):
        groups = {}
        for package in index.packages:
            group = groups.setdefault(package.feed.name, {})
            best = group.get(package.name)
            if best is None or compare_versions(package.version, best.version) > 0:
                group[package.name] = package
        units = []
        parents = {}
        for feed in self.manager.config.feeds:
            packages = groups.get(feed.name)
            if packages is None:
                continue
            units.append((_title(feed.segments), [self._package_task(package) for package in packages.values()], False))
            if len(feed.segments) > 1:
                parents.setdefault(feed.url.rsplit("/", 1)[0], feed.segments[:-1])
        for url, segments in parents.items():
            tasks = [self._root_task(url, segments, name) for name in ROOT_FILES]
            units.append((_title(segments), tasks, True))
        return units

    def _package_task(self, package):
        name = package.filename or package.name
        url = "%s/%s" % (package.feed.url, name)

        def action():
            return self.manager.sync_package(package).status

        return name, url, action

    def _root_task(self, base, segments, name):
        url = base + "/" + name

        def action():
            destination = safe_join(self.manager.mirror, *segments, name)
            try:
                data = self.manager.fetcher.get(url, MAX_ROOT_FILE_SIZE)
            except FetchError as error:
                if error.status == 404:
                    return None
                raise
            current = None
            if destination.is_file() and destination.stat().st_size <= MAX_ROOT_FILE_SIZE:
                current = destination.read_bytes()
            if current == data:
                return CURRENT
            write_atomic(destination, data)
            return DOWNLOADED if current is None else UPDATED

        return name, url, action

    def _run_unit(self, title, tasks, quiet):
        total = len(tasks)
        pending = queue.Queue()
        for task in tasks:
            pending.put(task)
        self.active = []
        self.done = self.failed = 0
        self.counts = {DOWNLOADED: 0, UPDATED: 0, CURRENT: 0}
        live = self.console.can_live() and not quiet and total > 0
        if not live and not quiet:
            self.console.write(title)
        started = time.monotonic()
        self.samples.clear()
        threads = [
            threading.Thread(target=self._work, args=(pending,), daemon=True)
            for _ in range(min(self.workers, total))
        ]
        for thread in threads:
            thread.start()
        while total:
            with self.lock:
                done, active = self.done, list(self.active)
            if live:
                self.console.live(self._frame(title, done, total, active, started))
            if done >= total:
                break
            time.sleep(REFRESH_INTERVAL)
        for thread in threads:
            thread.join()
        self.console.live_clear()
        if quiet and not any(self.counts.values()) and not self.failed:
            return
        if live or quiet:
            self.console.write(title)
        if self.failed:
            text = "Failed - %d of %d Files" % (self.failed, total)
            self.console.write(text)
        else:
            text = "Completed - %d Files Downloaded | %d Files Updated" % (self.counts[DOWNLOADED], self.counts[UPDATED])
            self.console.success(text)
        self.console.write("-" * (len(text) + 5))

    def _bar(self, done, total):
        icons = self.console.icons
        if icons != "unicode":
            filled = done * BAR_WIDTH // total
            full_char, empty_char = (BAR_FULL, BAR_EMPTY) if icons == "basic" else ("#", "-")
            return self.console.paint(full_char * filled, BAR_COLOR) + self.console.paint(empty_char * (BAR_WIDTH - filled), TRACK_COLOR)
        full, part = divmod(done * BAR_WIDTH * 8 // total, 8)
        fill = BAR_FULL * full + (BAR_PARTIAL[part] if part else "")
        return self.console.paint(fill, BAR_COLOR) + self.console.paint(BAR_EMPTY * (BAR_WIDTH - len(fill)), TRACK_COLOR)

    def _speed(self, now):
        received = self.manager.fetcher.received
        self.samples.append((now, received))
        while len(self.samples) > 2 and now - self.samples[0][0] > SPEED_WINDOW:
            self.samples.popleft()
        then, before = self.samples[0]
        bits = (received - before) * 8 / (now - then) if now > then else 0.0
        for scale, unit in SPEED_UNITS:
            if bits >= scale:
                value = bits / (scale or 1e3)
                return ("%.1f" if value < 10 else "%.0f") % value + " " + unit

    def _frame(self, title, done, total, active, started):
        now = time.monotonic()
        elapsed = now - started
        icons = self.console.icons
        spinner = SPINNER_UNICODE if icons == "unicode" else SPINNER_ASCII
        tick = spinner[int(elapsed / REFRESH_INTERVAL) % len(spinner)]
        status = "%s %s %3d%%  %d/%d  %s  %02d:%02d" % (
            self.console.paint(tick, SPINNER_COLOR),
            self._bar(done, total),
            done * 100 // total,
            done,
            total,
            self._speed(now),
            elapsed // 60,
            elapsed % 60,
        )
        arrow = self.console.paint(ARROW_UNICODE if icons != "ascii" else ARROW_ASCII, ARROW_COLOR)
        slots = min(self.workers, total)
        names = list(active[:slots])
        names += [""] * (slots - len(names))
        lines = [self.console.paint(title, TITLE_COLOR), status]
        lines += [("  %s %s" % (arrow, name)) if name else "" for name in names]
        return lines

    def _work(self, pending):
        while not self.stop.is_set():
            try:
                name, url, action = pending.get_nowait()
            except queue.Empty:
                return
            with self.lock:
                self.active.append(name)
            result = None
            reason = None
            try:
                result = self._attempt(action)
            except Exception as error:
                reason = "%s (%s)" % (getattr(error, "url", None) or url, error)
            with self.lock:
                self.active.remove(name)
                self.done += 1
                if reason is not None:
                    self.failed += 1
                    self.errors.append(reason)
                elif result is not None:
                    self.counts[result] += 1
            if result in (DOWNLOADED, UPDATED):
                self.stop.wait(REQUEST_DELAY)

    def _attempt(self, action):
        attempt = 0
        while True:
            attempt += 1
            self._respect_pause()
            try:
                return action()
            except FetchError as error:
                if not error.retry or attempt >= RETRIES or self.stop.is_set():
                    raise
                self._back_off(error, attempt)

    def _respect_pause(self):
        while not self.stop.is_set():
            wait = self.resume - time.monotonic()
            if wait <= 0:
                return
            self.stop.wait(wait)

    def _back_off(self, error, attempt):
        delay = error.delay or RETRY_BACKOFF * attempt
        if error.status in RATE_LIMIT_STATUSES:
            with self.lock:
                self.resume = max(self.resume, time.monotonic() + delay)
        else:
            self.stop.wait(delay)

    def _summary(self):
        count = len(self.errors)
        self.console.write("Task Completed | %d Errors" % count)
        for error in self.errors:
            self.console.line(error)
        return 1 if count else 0
