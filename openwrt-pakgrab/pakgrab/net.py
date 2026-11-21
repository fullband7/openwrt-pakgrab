import functools
import hashlib
import http.client
import os
import ssl
import tempfile
import threading
import urllib.request
import zlib
from base64 import b64encode
from urllib.parse import unquote, urljoin, urlsplit

USER_AGENT = "pkg-dl/1.0"
CHUNK_SIZE = 65536
REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
RETRY_STATUSES = frozenset((408, 425, 429, 500, 502, 503, 504))
MAX_REDIRECTS = 5
MAX_RETRY_AFTER = 60
MAX_IDLE = 32
MAX_DRAIN = 4096


class FetchError(Exception):
    def __init__(self, message, status=None, url=None, retry=False, delay=0):
        super().__init__(message)
        self.status = status
        self.url = url
        self.retry = retry
        self.delay = delay


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
    def __init__(self, allow_http=False, timeout=30):
        self.allow_http = allow_http
        self.timeout = timeout
        self.context = ssl.create_default_context()
        self.proxies = urllib.request.getproxies()
        self.lock = threading.Lock()
        self.idle = {}
        self.received = 0

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

    def _count(self, size):
        with self.lock:
            self.received += size

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

    def _send(self, url, parts):
        key = (parts.scheme, parts.hostname, parts.port)
        for attempt in (0, 1):
            entry = self._acquire(key)
            reused = entry is not None
            if entry is None:
                entry = self._connect(parts)
            connection, absolute, extra = entry
            target = url if absolute else (parts.path or "/") + ("?" + parts.query if parts.query else "")
            headers = dict(extra, **{"User-Agent": USER_AGENT})
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

    def _open(self, url):
        if not self._scheme_allowed(url):
            raise FetchError("insecure or unsupported URL scheme")
        for _ in range(MAX_REDIRECTS + 1):
            try:
                parts = urlsplit(url)
                parts.port
                lease, response = self._send(url, parts)
            except ValueError as error:
                raise FetchError(str(error) or "invalid URL")
            status = response.status
            if status == 200:
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
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                self._count(len(chunk))
                data.extend(chunk)
                if len(data) > limit:
                    raise FetchError("response too large")
            clean = True
            return bytes(data)
        except (OSError, http.client.HTTPException) as error:
            raise _network_error(error)
        finally:
            response.close()
            self._finish(lease, clean)

    @_tag_url
    def download(self, url, destination, limit, size=None, sha256=None, verify=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=destination.parent, prefix=".", suffix=".part")
        digest = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                lease, response = self._open(url)
                clean = False
                try:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        self._count(len(chunk))
                        total += len(chunk)
                        if total > limit:
                            raise FetchError("file too large")
                        digest.update(chunk)
                        handle.write(chunk)
                    clean = True
                except (OSError, http.client.HTTPException) as error:
                    raise _network_error(error)
                finally:
                    response.close()
                    self._finish(lease, clean)
            if size is not None and total != size:
                raise FetchError("size mismatch")
            if sha256 is not None and digest.hexdigest() != sha256.lower():
                raise FetchError("SHA256 checksum mismatch")
            if verify is not None:
                verify(temp)
            os.chmod(temp, 0o644)
            os.replace(temp, destination)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
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
