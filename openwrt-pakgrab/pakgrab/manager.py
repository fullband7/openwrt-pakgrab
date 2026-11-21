import fnmatch
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import adb, usign
from .fs import PathError, display, safe_join, write_atomic
from .index import Index, parse_packages
from .mirror import Mirror
from .net import FetchError, gunzip
from .versions import CURRENT, DOWNLOADED, UPDATED, Outcome, identify_ipk, newest, scan

MAX_INDEX_SIZE = 64 * 1024 * 1024
MAX_PACKAGE_SIZE = 512 * 1024 * 1024
MAX_SIGNATURE_SIZE = 4096
MAX_PATTERN_LENGTH = 128
MAX_WORKERS = 4
LIST_FILES = ("Packages.gz", "Packages", "Packages.sig")


class Manager:
    command = "opkg"

    def __init__(self, config, mirror, keys, fetcher, console):
        self.config = config
        self.mirror = Path(mirror)
        self.keys = keys
        self.fetcher = fetcher
        self.console = console
        self.catalog = {}
        self.names = {}
        self.catalog_lock = threading.Lock()
        self.on_feed = None

    def _mark(self, feed, ok):
        if self.on_feed is not None:
            self.on_feed(feed.name, ok)

    def _feed_dir(self, feed):
        return safe_join(self.mirror, *feed.segments)

    def _code(self, name):
        return "%s_%s" % (self.command, name)

    def _report(self, errors):
        if not errors:
            return 0
        self.console.write("Collected errors:")
        for error in errors:
            self.console.write(" * " + error)
        return 1

    def _fetch_index(self, feed):
        url = feed.url + "/Packages.gz"
        self.console.write("Downloading " + url)
        try:
            raw = self.fetcher.get(url, MAX_INDEX_SIZE)
            return "Packages.gz", raw, gunzip(raw, MAX_INDEX_SIZE), url
        except FetchError as error:
            if error.status != 404:
                raise
        url = feed.url + "/Packages"
        self.console.write("Downloading " + url)
        raw = self.fetcher.get(url, MAX_INDEX_SIZE)
        return "Packages", raw, raw, url

    def _snapshot(self, directory):
        saved = {}
        for name in LIST_FILES:
            try:
                saved[name] = self._read(directory / name, MAX_INDEX_SIZE)
            except (FetchError, OSError):
                continue
        return saved

    @staticmethod
    def _restore(directory, saved):
        for name in LIST_FILES:
            path = directory / name
            try:
                if name in saved:
                    write_atomic(path, saved[name])
                elif path.is_file():
                    path.unlink()
            except OSError:
                continue

    def _update_feed(self, feed, errors):
        url = feed.url
        directory = None
        saved = {}
        verified = False
        try:
            directory = self._feed_dir(feed)
            saved = self._snapshot(directory)
            name, raw, content, url = self._fetch_index(feed)
            write_atomic(directory / name, raw)
            stale = directory / ("Packages" if name == "Packages.gz" else "Packages.gz")
            if stale.is_file():
                stale.unlink()
            self.console.write("Updated list of available packages in " + display(directory / name))
            url = feed.url + "/Packages.sig"
            self.console.write("Downloading " + url)
            signature = self.fetcher.get(url, MAX_SIGNATURE_SIZE)
            try:
                usign.verify(content, signature, self.keys)
            except usign.SignatureError as error:
                self.console.write("Signature check failed.")
                self.console.write("Remove wrong Signature file.")
                errors.append("%s: %s: %s" % (self._code("verify"), feed.name, error))
                return
            write_atomic(directory / "Packages.sig", signature)
            verified = True
            self.console.write("Signature check passed.")
        except FetchError as error:
            errors.append("%s: Failed to download %s: %s" % (self._code("download"), error.url or url, error))
        except (PathError, OSError) as error:
            errors.append("%s: %s: %s" % (self._code("update"), feed.name, error))
        finally:
            if directory is not None and not verified:
                self._restore(directory, saved)
            self._mark(feed, verified)

    def update(self):
        errors = []
        for feed in self.config.feeds:
            self._update_feed(feed, errors)
        return self._report(errors)

    @staticmethod
    def _read(path, limit):
        if path.stat().st_size > limit:
            raise FetchError("file too large")
        return path.read_bytes()

    def _read_verified(self, directory):
        signature = self._read(directory / "Packages.sig", MAX_SIGNATURE_SIZE)
        compressed = directory / "Packages.gz"
        if compressed.is_file():
            content = gunzip(self._read(compressed, MAX_INDEX_SIZE), MAX_INDEX_SIZE)
        else:
            content = self._read(directory / "Packages", MAX_INDEX_SIZE)
        usign.verify(content, signature, self.keys)
        return content

    def _load_feed(self, feed):
        content = self._read_verified(self._feed_dir(feed))
        return parse_packages(content.decode("utf-8", "replace"), feed)

    @staticmethod
    def _label(package, url):
        return url

    def load_index(self):
        packages = []
        loaded = 0
        problems = []
        for feed in self.config.feeds:
            try:
                feed_packages = self._load_feed(feed)
            except FileNotFoundError:
                problems.append("%s: no package list, run '%s update'" % (feed.name, self.command))
            except usign.SignatureError as error:
                problems.append("%s: signature check failed (%s)" % (feed.name, error))
            except (adb.AdbError, FetchError, PathError, OSError) as error:
                problems.append("%s: %s" % (feed.name, error))
            else:
                packages.extend(feed_packages)
                loaded += 1
        return Index(packages), loaded, problems

    def _ready_index(self):
        index, loaded, problems = self.load_index()
        self.catalog = {}
        self.names = {}
        for package in index.packages:
            self.names.setdefault(package.feed.name, set()).add(package.name)
        for problem in problems:
            self.console.write("%s: skipping feed %s" % (self.command, problem))
        if not loaded:
            self.console.write("No package lists available. Run '%s update' first." % self.command)
            return None
        return index

    @staticmethod
    def _identify(filename, names):
        return identify_ipk(filename, names)

    @staticmethod
    def _require(package):
        if not package.filename:
            raise FetchError("invalid or missing Filename in index")
        if not package.sha256:
            raise FetchError("missing SHA256sum in index")

    def _fetch(self, package, destination, url):
        self.fetcher.download(url, destination, MAX_PACKAGE_SIZE, package.size or None, package.sha256)

    def _copies(self, package):
        with self.catalog_lock:
            found = self.catalog.get(package.feed.name)
            if found is None:
                found = scan(self._feed_dir(package.feed), self._identify, self.names.get(package.feed.name, ()))
                self.catalog[package.feed.name] = found
            return list(found.get(package.name, ()))

    def sync_package(self, package):
        self._require(package)
        destination = safe_join(self.mirror, *package.feed.segments, package.filename)
        url = package.feed.url + "/" + package.filename
        stale = [(version, path) for version, path in self._copies(package) if path != destination]
        if destination.is_file():
            for version, path in stale:
                path.unlink()
            return Outcome(CURRENT, destination, None, package.version)
        self._fetch(package, destination, url)
        for version, path in stale:
            path.unlink()
        if stale:
            return Outcome(UPDATED, destination, url, newest([version for version, path in stale]))
        return Outcome(DOWNLOADED, destination, url, "")

    def _transfer(self, package):
        try:
            outcome = self.sync_package(package)
        except FetchError as error:
            return package, None, "%s: Failed to download %s: %s" % (self._code("download"), package.name, error)
        except (PathError, OSError) as error:
            return package, None, "%s: %s: %s" % (self._code("download"), package.name, error)
        return package, outcome, None

    def download(self, names, with_dependencies=True, skip_unknown=False):
        index = self._ready_index()
        if index is None:
            return 1
        for name in names:
            if index.is_virtual(name):
                self.console.write("%s is a virtual package provided by the firmware, nothing to download." % name)
        names = [name for name in names if not index.is_virtual(name)]
        if not names:
            return 0
        ordered, unknown, missing = index.resolve(names, with_dependencies)
        for name in unknown:
            self.console.write("Unknown package '%s'." % name)
        if unknown and (not skip_unknown or not ordered):
            return 1
        errors = []
        counts = {DOWNLOADED: 0, UPDATED: 0, CURRENT: 0}
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(ordered))) as pool:
            for package, outcome, error in pool.map(self._transfer, ordered):
                if error:
                    errors.append(error)
                    continue
                counts[outcome.status] += 1
                if outcome.status == CURRENT:
                    self.console.write("%s %s is already up to date as %s" % (package.name, outcome.previous, display(outcome.destination)))
                    continue
                self.console.write("Downloading " + self._label(package, outcome.url))
                if outcome.status == UPDATED:
                    self.console.write("Updated %s (%s -> %s) as %s" % (package.name, outcome.previous, package.version, display(outcome.destination)))
                else:
                    self.console.write("Downloaded %s as %s" % (package.name, display(outcome.destination)))
        for requirer, dependency in missing:
            errors.append("%s: Cannot satisfy dependency '%s' required by %s" % (self._code("resolve"), dependency, requirer))
        status = self._report(errors) or int(bool(unknown))
        summary = "%d Files Downloaded | %d Files Updated" % (counts[DOWNLOADED], counts[UPDATED])
        if status:
            self.console.write(summary)
        else:
            self.console.success(summary)
        return status

    def mirror_all(self):
        return Mirror(self).run()

    def list_packages(self, pattern=None):
        index = self._ready_index()
        if index is None:
            return 1
        if pattern is not None:
            pattern = pattern.lower()[:MAX_PATTERN_LENGTH]
            if not any(char in pattern for char in "*?["):
                pattern = "*" + pattern + "*"
        for name in sorted(index.by_name):
            if pattern is not None and not fnmatch.fnmatchcase(name.lower(), pattern):
                continue
            package = index.by_name[name]
            summary = package.description.split("\n", 1)[0]
            self.console.write("%s - %s - %s" % (package.name, package.version, summary))
        return 0

    def info(self, name):
        index = self._ready_index()
        if index is None:
            return 1
        package = index.lookup(name)
        if package is None:
            self.console.write("Unknown package '%s'." % name)
            return 1
        depends = ", ".join(" | ".join(group) for group in package.depends)
        rows = (
            ("Package", package.name),
            ("Version", package.version),
            ("Depends", depends),
            ("Provides", ", ".join(package.provides)),
            ("Section", package.section),
            ("Architecture", package.architecture),
            ("Size", str(package.size) if package.size else ""),
            ("Filename", package.filename),
            ("Feed", package.feed.name),
        )
        for label, value in rows:
            if value:
                self.console.write("%s: %s" % (label, value))
        if package.description:
            self.console.write("Description: " + package.description)
        return 0

    def feeds(self):
        for feed in self.config.feeds:
            self.console.write("%s %s" % (feed.name, feed.url))
        return 0
