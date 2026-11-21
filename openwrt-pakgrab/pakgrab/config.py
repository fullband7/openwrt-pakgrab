import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

FEED_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~=@-]{0,127}$")
IPK_LINE_RE = re.compile(r"^src(?:/gz)?\s+(\S+)\s+(\S+)$")
APK_LINE_RE = re.compile(r"^(?:@\S+\s+)?(https?://\S+)/packages\.adb$")
MAX_CONFIG_SIZE = 1024 * 1024
MAX_URL_LENGTH = 2048
MAX_SEGMENTS = 16
CONFIG_PATTERNS = ("distfeeds.conf", "distfeeds.list", "*.conf", "*.list")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    manager: str
    segments: tuple
    arch: str
    release: str


@dataclass(frozen=True)
class Config:
    feeds: tuple
    manager: str
    arch: str
    release: str
    source: Path
    warnings: tuple


def _split_url(url):
    if len(url) > MAX_URL_LENGTH:
        raise ConfigError("URL too long")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port
    except ValueError:
        raise ConfigError("invalid URL")
    if parts.scheme not in ("http", "https") or not hostname:
        raise ConfigError("only http and https URLs are supported")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError("URL must not contain credentials, query or fragment")
    segments = tuple(part for part in parts.path.split("/") if part)
    if not segments or len(segments) > MAX_SEGMENTS:
        raise ConfigError("URL path is empty or too deep")
    for segment in segments:
        if not SEGMENT_RE.match(segment):
            raise ConfigError("unsafe character in URL path")
    base = parts.scheme + "://" + parts.netloc + "/" + "/".join(segments)
    return base, segments


def _detect_arch(segments):
    if "packages" in segments:
        index = segments.index("packages")
        if index + 1 < len(segments):
            return segments[index + 1]
    return ""


def _detect_release(segments):
    if "releases" in segments:
        index = segments.index("releases")
        if index + 1 < len(segments):
            return segments[index + 1]
    if "snapshots" in segments:
        return "SNAPSHOT"
    return ""


def _apk_name(segments):
    if "kmods" in segments:
        return "apk_kmods"
    if segments[-1] == "packages" and "targets" in segments:
        return "apk_core"
    return "apk_" + segments[-1]


def _make_feed(name, url, manager):
    base, segments = _split_url(url)
    return Feed(name, base, manager, segments, _detect_arch(segments), _detect_release(segments))


def parse(text):
    feeds = []
    warnings = []
    names = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            match = IPK_LINE_RE.match(line)
            if match:
                name, url = match.groups()
                if not FEED_NAME_RE.match(name):
                    raise ConfigError("invalid feed name")
                feed = _make_feed(name, url, "IPK")
            else:
                match = APK_LINE_RE.match(line)
                if not match:
                    continue
                feed = _make_feed("apk", match.group(1), "APK")
                name = base = _apk_name(feed.segments)
                counter = 1
                while name in names:
                    counter += 1
                    name = "%s_%d" % (base, counter)
                feed = Feed(name, feed.url, feed.manager, feed.segments, feed.arch, feed.release)
        except ConfigError as error:
            warnings.append("line %d: %s" % (number, error))
            continue
        if feed.name in names:
            warnings.append("line %d: duplicate feed name %s" % (number, feed.name))
            continue
        names.add(feed.name)
        feeds.append(feed)
    return feeds, warnings


def load(path):
    path = Path(path)
    try:
        if path.stat().st_size > MAX_CONFIG_SIZE:
            raise ConfigError("%s is too large" % path.name)
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ConfigError("cannot read %s: %s" % (path, error.strerror))
    feeds, warnings = parse(text)
    if not feeds:
        raise ConfigError("no usable feeds found in %s" % path.name)
    manager = feeds[0].manager
    selected = [feed for feed in feeds if feed.manager == manager]
    if len(selected) != len(feeds):
        warnings.append("mixed IPK and APK feeds, using %s only" % manager)
    arch = next((feed.arch for feed in selected if feed.arch), "unknown")
    release = next((feed.release for feed in selected if feed.release), "unknown")
    return Config(tuple(selected), manager, arch, release, path, tuple(warnings))


def discover(directories, explicit=None):
    if explicit:
        return load(explicit)
    seen = set()
    for directory in directories:
        directory = Path(directory)
        for pattern in CONFIG_PATTERNS:
            for candidate in sorted(directory.glob(pattern)):
                if candidate in seen or not candidate.is_file():
                    continue
                seen.add(candidate)
                try:
                    return load(candidate)
                except ConfigError:
                    continue
    raise ConfigError("no distfeeds.conf found next to the program")
