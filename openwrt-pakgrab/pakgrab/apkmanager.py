from . import adb
from .fs import PathError, write_atomic
from .manager import MAX_INDEX_SIZE, MAX_PACKAGE_SIZE, Manager
from .net import FetchError
from .versions import identify_apk

INDEX_NAME = "packages.adb"


class ApkManager(Manager):
    command = "apk"

    def _load_feed(self, feed):
        raw = self._read(self._feed_dir(feed) / INDEX_NAME, MAX_INDEX_SIZE)
        return adb.parse_index(raw, self.keys, feed, MAX_INDEX_SIZE)[0]

    @staticmethod
    def _label(package, url):
        return "%s-%s" % (package.name, package.version)

    def _warn(self, action, url, error):
        self.console.write("WARNING: %s %s: %s" % (action, url, error))

    def update(self):
        available = []
        distinct = set()
        unavailable = 0
        for feed in self.config.feeds:
            url = feed.url + "/" + INDEX_NAME
            self.console.progress("[fetch %s]" % url)
            try:
                directory = self._feed_dir(feed)
                raw = self.fetcher.get(url, MAX_INDEX_SIZE)
                packages, description = adb.parse_index(raw, self.keys, feed, MAX_INDEX_SIZE)
                write_atomic(directory / INDEX_NAME, raw)
            except (FetchError, adb.AdbError) as error:
                unavailable += 1
                self._mark(feed, False)
                self._warn("fetching", url, error)
                continue
            except (PathError, OSError) as error:
                unavailable += 1
                self._mark(feed, False)
                self._warn("updating", url, error)
                continue
            self._mark(feed, True)
            available.append((description, url))
            distinct.update(package.identity or (package.name, package.version) for package in packages)
        self.console.progress_end()
        for description, url in available:
            self.console.cut("%s [%s]" % (description, url))
        status = "%d unavailable, 0 stale;" % unavailable if unavailable else "OK:"
        self.console.write("%s %d distinct packages available" % (status, len(distinct)))
        return 1 if unavailable else 0

    @staticmethod
    def _identify(filename, names):
        return identify_apk(filename, names)

    @staticmethod
    def _require(package):
        if not package.filename:
            raise FetchError("invalid or missing filename in index")
        if not package.identity:
            raise FetchError("missing package hash in index")

    def _check(self, path, package):
        try:
            adb.verify_package_file(path, package.identity, MAX_PACKAGE_SIZE)
        except adb.AdbError as error:
            raise FetchError(str(error))

    def _fetch(self, package, destination, url):
        self.fetcher.download(
            url, destination, MAX_PACKAGE_SIZE, package.size or None, None, lambda path: self._check(path, package)
        )
