import fnmatch
import re
from dataclasses import dataclass, field

from .index import compare_versions

ARCH_INDEPENDENT = frozenset(("all", "noarch"))
TOKEN_RE = re.compile(r"^[A-Za-z0-9_*?\[\].+-]{1,128}$")
MAX_TOKENS = 256


class SelectionError(Exception):
    pass


def split_tokens(values):
    tokens = []
    for value in values:
        tokens.extend(part.strip() for part in value.split(",") if part.strip())
    tokens = tuple(dict.fromkeys(tokens))
    if len(tokens) > MAX_TOKENS:
        raise SelectionError("too many values")
    for token in tokens:
        if not TOKEN_RE.match(token):
            raise SelectionError("invalid value '%s'" % token[:64])
    return tokens


def feed_aliases(name):
    lowered = name.lower()
    aliases = [lowered]
    position = lowered.find("_")
    while position != -1:
        aliases.append(lowered[position + 1:])
        position = lowered.find("_", position + 1)
    return aliases


@dataclass
class Plan:
    feeds: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    left_out: int = 0

    @property
    def count(self):
        return sum(len(packages) for _feed, packages in self.feeds)

    @property
    def size(self):
        return sum(package.size for _feed, packages in self.feeds for package in packages)


@dataclass(frozen=True)
class Selection:
    feeds: tuple = ()
    arches: tuple = ()
    packages: tuple = ()
    dependencies: bool = True

    @property
    def active(self):
        return bool(self.feeds or self.arches or self.packages)

    def describe(self):
        parts = []
        if self.feeds:
            parts.append("feeds=" + ",".join(self.feeds))
        if self.arches:
            parts.append("arch=" + ",".join(self.arches))
        if self.packages:
            parts.append("packages=%d%s" % (len(self.packages), "" if self.dependencies else " (no deps)"))
        return " ".join(parts) or "everything"

    def pick_feeds(self, feeds):
        if not self.feeds:
            return list(feeds)
        keep = set()
        unknown = []
        for token in self.feeds:
            pattern = token.lower()
            found = [feed for feed in feeds if any(fnmatch.fnmatchcase(alias, pattern) for alias in feed_aliases(feed.name))]
            keep.update(found)
            if not found:
                unknown.append(token)
        if unknown:
            raise SelectionError("unknown feed %s, available: %s" % (", ".join(unknown), ", ".join(feed.name for feed in feeds)))
        return [feed for feed in feeds if feed in keep]

    def accepts_arch(self, package):
        if not self.arches:
            return True
        arch = package.architecture.lower()
        if not arch or arch in ARCH_INDEPENDENT:
            return True
        return any(fnmatch.fnmatchcase(arch, pattern.lower()) for pattern in self.arches)

    def build(self, index, feeds):
        chosen = self.pick_feeds(feeds)
        names = {feed.name for feed in chosen}
        wanted = None
        unknown = []
        missing = []
        if self.packages:
            ordered, unknown, missing = index.resolve(list(self.packages), self.dependencies)
            wanted = {(package.feed.name, package.name) for package in ordered}
        groups = {}
        excluded = set()
        for package in index.packages:
            key = (package.feed.name, package.name)
            if wanted is not None and key not in wanted:
                continue
            if package.feed.name not in names or not self.accepts_arch(package):
                excluded.add(key)
                continue
            group = groups.setdefault(package.feed.name, {})
            best = group.get(package.name)
            if best is None or compare_versions(package.version, best.version) > 0:
                group[package.name] = package
        selected = [(feed, list(groups[feed.name].values())) for feed in chosen if feed.name in groups]
        left_out = len(excluded) if wanted is not None else 0
        return Plan(selected, unknown, missing, left_out)
