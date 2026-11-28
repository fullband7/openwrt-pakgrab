import functools
import hashlib
import http.client
import os
import re
import ssl
import threading
import urllib.request
import zlib
from base64 import b64encode
from urllib.parse import unquote, urljoin, urlsplit

from .progress import Tracker
from .settings import Settings
from .throttle import RateLimiter, chunk_size

USER_AGENT = "pkg-dl/1.0"
CHUNK_SIZE = 65536
REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
RETRY_STATUSES = frozenset((408, 425, 429, 500, 502, 503, 504))
MAX_REDIRECTS = 5
MAX_RETRY_AFTER = 60
MAX_IDLE = 32
MAX_DRAIN = 4096
CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$")


class FetchError(Exception):
    def __init__(self, message, status=None, url=None, retry=False, delay=0):
        super().__init__(message)
        self.status = status
        self.url = url
        self.retry = retry
        self.delay = delay


class Cancelled(FetchError):
    def __init__(self):
        super().__init__("cancelled")


def _tag_url(method):
    @functools.wraps(method)
    def wrapper(self, url, *args, **kwargs):
        try:
            return method(self, url, *args, **kwargs)
        except FetchError as error:
            if error.url is None:
                error.url = url
            raise
    return wrapper


def _retry_delay(value):
    if value and value.strip().isdigit():
        return min(int(value), MAX_RETRY_AFTER)
    return 0


def _network_error(error):
    retry = not isinstance(error, ssl.SSLCertVerificationError)
    return FetchError(str(error) or error.__class__.__name__, retry=retry)


class Fetcher:
    def __init__(self, allow_http=False, timeout=30, settings=None):
        self.allow_http = allow_http
        self.timeout = timeout
        self.settings = settings or Settings()
        self.context = ssl.create_default_context()
        self.proxies = urllib.request.getproxies()
        self.lock = threading.Lock()
        self.idle = {}
        self.limiter = RateLimiter()
        self.tracker = Tracker()
        self.cancel = threading.Event()

    def _acquire(self, key):
        with self.lock:
            idle = self.idle.get(key)
            return idle.pop() if idle else None

    def _finish(self, lease, reusable):
        key, entry = lease
        if reusable:
            with self.lock:
                idle = self.idle.setdefault(key, [])
                if len(idle) < MAX_IDLE:
                    idle.append(entry)
                    return
        entry[0].close()

    def _account(self, size):
        self.tracker.advance(size)
        self.limiter.acquire(size, self.settings.rate, self.cancel)
        if self.cancel.is_set():
            raise Cancelled()

    def _chunk(self):
        return chunk_size(self.settings.rate, CHUNK_SIZE)

    def close(self):
        with self.lock:
            entries = [entry for idle in self.idle.values() for entry in idle]
            self.idle.clear()
        for entry in entries:
            entry[0].close()

    def _proxy(self, parts):
        value = self.proxies.get(parts.scheme)
        if not value or urllib.request.proxy_bypass(parts.hostname):
            return None
        if "://" not in value:
            value = "http://" + value
        proxy = urlsplit(value)
        if not proxy.hostname:
            return None
        headers = {}
        if proxy.username:
            credentials = "%s:%s" % (unquote(proxy.username), unquote(proxy.password or ""))
            headers["Proxy-Authorization"] = "Basic " + b64encode(credentials.encode("utf-8")).decode("ascii")
        return proxy.hostname, proxy.port or 80, headers

    def _connect(self, parts):
        host, port = parts.hostname, parts.port
        proxy = self._proxy(parts)
        if parts.scheme == "https":
            if proxy is None:
                return http.client.HTTPSConnection(host, port, timeout=self.timeout, context=self.context), False, {}
            connection = http.client.HTTPSConnection(proxy[0], proxy[1], timeout=self.timeout, context=self.context)
            connection.set_tunnel(host, port, proxy[2])
            return connection, False, {}
        if proxy is None:
            return http.client.HTTPConnection(host, port, timeout=self.timeout), False, {}
        return http.client.HTTPConnection(proxy[0], proxy[1], timeout=self.timeout), True, proxy[2]

    def _send(self, url, parts, extra_headers=None):
        key = (parts.scheme, parts.hostname, parts.port)
        for attempt in (0, 1):
            entry = self._acquire(key)
            reused = entry is not None
            if entry is None:
                entry = self._connect(parts)
            connection, absolute, extra = entry
            target = url if absolute else (parts.path or "/") + ("?" + parts.query if parts.query else "")
            headers = dict(extra, **{"User-Agent": USER_AGENT})
            if extra_headers:
                headers.update(extra_headers)
            try:
                connection.request("GET", target, headers=headers)
                return (key, entry), connection.getresponse()
            except (OSError, http.client.HTTPException) as error:
                connection.close()
                if reused and attempt == 0 and isinstance(error, (ConnectionError, http.client.BadStatusLine)):
                    continue
                raise _network_error(error)

    def _scheme_allowed(self, url):
        scheme = urlsplit(url).scheme
        return scheme == "https" or (scheme == "http" and self.allow_http)

    def _open(self, url, offset=0):
        if not self._scheme_allowed(url):
            raise FetchError("insecure or unsupported URL scheme")
        extra = {"Range": "bytes=%d-" % offset} if offset else None
        for _ in range(MAX_REDIRECTS + 1):
            try:
                parts = urlsplit(url)
                parts.port
                lease, response = self._send(url, parts, extra)
            except ValueError as error:
                raise FetchError(str(error) or "invalid URL")
            status = response.status
            if status == 200 or (status == 206 and offset):
                return lease, response
            location = response.getheader("Location")
            delay = _retry_delay(response.getheader("Retry-After"))
            try:
                response.read(MAX_DRAIN)
                drained = response.isclosed()
            except (OSError, http.client.HTTPException):
                drained = False
            response.close()
            self._finish(lease, drained)
            if status not in REDIRECT_STATUSES:
                raise FetchError("HTTP %d" % status, status, retry=status in RETRY_STATUSES, delay=delay)
            if not location:
                raise FetchError("HTTP %d" % status, status)
            url = urljoin(url, location)
            if not self._scheme_allowed(url):
                raise FetchError("redirect to a disallowed scheme refused")
        raise FetchError("too many redirects")

    @_tag_url
    def get(self, url, limit):
        lease, response = self._open(url)
        clean = False
        try:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > limit:
                raise FetchError("response too large")
            data = bytearray()
            while True:
                chunk = response.read(self._chunk())
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise FetchError("response too large")
                self._account(len(chunk))
            clean = True
            return bytes(data)
        except (OSError, http.client.HTTPException) as error:
            raise _network_error(error)
        finally:
            response.close()
            self._finish(lease, clean)

    def _resume_point(self, partial, size):
        if not self.settings.resume:
            self._discard(partial)
            return 0
        try:
            offset = partial.stat().st_size
        except OSError:
            return 0
        if offset <= 0 or (size and offset > size):
            self._discard(partial)
            return 0
        return offset

    @staticmethod
    def _discard(partial):
        try:
            partial.unlink()
        except OSError:
            pass

    @staticmethod
    def _rehash(partial, digest, offset):
        remaining = offset
        with open(partial, "rb") as handle:
            while remaining:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise FetchError("partial file changed while resuming", retry=True)
                digest.update(chunk)
                remaining -= len(chunk)

    @staticmethod
    def _range_start(response):
        match = CONTENT_RANGE_RE.match((response.getheader("Content-Range") or "").strip())
        return int(match.group(1)) if match else -1

    def _transfer(self, url, partial, offset, limit, size):
        digest = hashlib.sha256()
        if size and offset == size:
            self._rehash(partial, digest, offset)
            self.tracker.seed(offset)
            return digest, offset
        lease, response = self._open(url, offset)
        clean = False
        try:
            if offset and response.status == 206 and self._range_start(response) != offset:
                self._discard(partial)
                raise FetchError("unexpected range in server response", retry=True)
            resumed = offset > 0 and response.status == 206
            if resumed:
                self._rehash(partial, digest, offset)
            else:
                offset = 0
            self.tracker.seed(offset)
            total = offset
            with open(partial, "ab" if resumed else "wb") as handle:
                while True:
                    if self.cancel.is_set():
                        raise Cancelled()
                    try:
                        chunk = response.read(self._chunk())
                    except (OSError, http.client.HTTPException) as error:
                        raise _network_error(error)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise FetchError("file too large")
                    digest.update(chunk)
                    handle.write(chunk)
                    self._account(len(chunk))
            clean = True
            return digest, total
        finally:
            response.close()
            self._finish(lease, clean)

    @staticmethod
    def _expect(total, size, digest, sha256):
        if size is not None and total < size:
            return FetchError("incomplete download (%d of %d bytes)" % (total, size), retry=True), False
        if size is not None and total != size:
            return FetchError("size mismatch"), True
        if sha256 is not None and digest.hexdigest() != sha256.lower():
            return FetchError("SHA256 checksum mismatch"), True
        return None, False

    @_tag_url
    def download(self, url, destination, limit, size=None, sha256=None, verify=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name("." + destination.name + ".part")
        offset = self._resume_point(partial, size)
        resumed = offset > 0
        try:
            try:
                digest, total = self._transfer(url, partial, offset, limit, size)
            except FetchError as error:
                if error.status != 416 or not offset:
                    raise
                self._discard(partial)
                resumed = False
                digest, total = self._transfer(url, partial, 0, limit, size)
            problem, final = self._expect(total, size, digest, sha256)
            if problem is not None:
                if final:
                    self._discard(partial)
                    problem.retry = resumed
                raise problem
            if verify is not None:
                try:
                    verify(partial)
                except FetchError as error:
                    self._discard(partial)
                    error.retry = resumed
                    raise
            os.chmod(partial, 0o644)
            os.replace(partial, destination)
        except FetchError as error:
            if not (error.retry or isinstance(error, Cancelled)):
                self._discard(partial)
            raise


def gunzip(data, limit):
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        result = decoder.decompress(data, limit + 1)
    except zlib.error:
        raise FetchError("corrupt gzip data")
    if len(result) > limit or decoder.unconsumed_tail:
        raise FetchError("decompressed index too large")
    if not decoder.eof:
        raise FetchError("truncated gzip data")
    return result
