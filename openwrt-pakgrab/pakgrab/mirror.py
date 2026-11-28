import time

from .fs import safe_join, write_atomic
from .net import FetchError
from .progress import format_rate, format_size
from .runner import Job, Runner
from .selection import Selection
from .versions import CURRENT, DOWNLOADED, UPDATED

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
    def __init__(self, manager, selection=None):
        self.manager = manager
        self.console = manager.console
        self.selection = selection or Selection()
        self.errors = []
        self.totals = {DOWNLOADED: 0, UPDATED: 0, CURRENT: 0}
        self.failed = 0
        self.bytes = 0

    def prepare(self):
        index = self.manager._ready_index()
        if index is None:
            return None
        return self.selection.build(index, self.manager.config.feeds)

    def describe(self, plan):
        write = self.console.write
        if self.selection.active:
            write("Selection: " + self.selection.describe())
        write("Selected %d feeds | %d packages | %s" % (len(plan.feeds), plan.count, format_size(plan.size)))
        for name in plan.unknown:
            write("Unknown package '%s'." % name)
        for requirer, dependency in plan.missing:
            write("%s: Cannot satisfy dependency '%s' required by %s" % (self.manager._code("resolve"), dependency, requirer))
        if plan.left_out:
            write("%d required packages are outside the selected feeds or architecture and were left out." % plan.left_out)

    def question(self, plan):
        return "Mirror %d packages (%s) from %d feeds? (y/n)" % (plan.count, format_size(plan.size), len(plan.feeds))

    def run(self, plan=None):
        plan = plan or self.prepare()
        if plan is None:
            return 1
        if not plan.count:
            self.console.write("No packages match the selection.")
            return 1
        runner = Runner(self.console, self.manager.fetcher, self.manager.settings.jobs)
        started = time.monotonic()
        try:
            for title, jobs, quiet in self._units(plan):
                self._run_unit(runner, title, jobs, quiet)
        finally:
            self.console.live_clear()
            self.manager.fetcher.close()
        return self._summary(plan, time.monotonic() - started)

    def _units(self, plan):
        units = []
        parents = {}
        for feed, packages in plan.feeds:
            units.append((_title(feed.segments), [self._package_job(package) for package in packages], False))
            if len(feed.segments) > 1:
                parents.setdefault(feed.url.rsplit("/", 1)[0], feed.segments[:-1])
        for url, segments in parents.items():
            jobs = [self._root_job(url, segments, name) for name in ROOT_FILES]
            units.append((_title(segments), jobs, True))
        return units

    def _package_job(self, package):
        name = package.filename or package.name
        url = "%s/%s" % (package.feed.url, name)

        def action():
            return self.manager.sync_package(package).status

        return Job(name, url, action, package.size)

    def _root_job(self, base, segments, name):
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

        return Job(name, url, action)

    def _run_unit(self, runner, title, jobs, quiet):
        batch = runner.run(title, jobs, quiet)
        total = len(batch.jobs)
        self.errors.extend(batch.errors)
        self.failed += batch.failed
        if not quiet:
            for status, count in batch.counts.items():
                self.totals[status] += count
        self.bytes += sum(result.job.size for result in batch.results if result.status in (DOWNLOADED, UPDATED))
        if quiet and not any(batch.counts.values()) and not batch.failed:
            return
        if quiet or self.console.can_live():
            self.console.write(title)
        if batch.failed:
            text = "Failed - %d of %d Files" % (batch.failed, total)
            self.console.write(text)
        else:
            text = "Completed - %d Files Downloaded | %d Files Updated" % (batch.counts[DOWNLOADED], batch.counts[UPDATED])
            self.console.success(text)
        self.console.write("-" * (len(text) + 5))

    def _summary(self, plan, elapsed):
        count = len(self.errors)
        self.console.write("Task Completed | %d Errors" % count)
        for error in self.errors:
            self.console.line(error)
        average = self.bytes / elapsed if elapsed > 0 else 0
        self.console.write(
            "Downloaded %d | Updated %d | Current %d | Failed %d | %s in %.0fs (%s avg)"
            % (
                self.totals[DOWNLOADED],
                self.totals[UPDATED],
                self.totals[CURRENT],
                self.failed,
                format_size(self.bytes),
                elapsed,
                format_rate(average),
            )
        )
        return 1 if count or plan.unknown else 0
