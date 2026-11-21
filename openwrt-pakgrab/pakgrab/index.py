import re
from collections import deque
from dataclasses import dataclass

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~=@-]{0,200}\.ipk$")
FILENAME_RE_APK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~=@-]{0,200}\.apk$")
DEPENDENCY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._+-]*)\s*(?:\([^()]*\))?$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_FIELD_LENGTH = 16384
BUILTIN = frozenset(("libc", "kernel"))


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    depends: tuple
    provides: tuple
    filename: str
    size: int
    sha256: str
    architecture: str
    section: str
    description: str
    feed: object
    identity: str = ""


def _order(char):
    if not char or char.isdigit():
        return 0
    if char == "~":
        return -1
    if char.isalpha():
        return ord(char)
    return ord(char) + 256


def _at(text, position):
    return text[position] if position < len(text) else ""


def compare_versions(left, right):
    i = j = 0
    while i < len(left) or j < len(right):
        while (_at(left, i) and not _at(left, i).isdigit()) or (_at(right, j) and not _at(right, j).isdigit()):
            a, b = _order(_at(left, i)), _order(_at(right, j))
            if a != b:
                return -1 if a < b else 1
            i += 1
            j += 1
        while _at(left, i) == "0":
            i += 1
        while _at(right, j) == "0":
            j += 1
        difference = 0
        while _at(left, i).isdigit() and _at(right, j).isdigit():
            if not difference:
                difference = ord(left[i]) - ord(right[j])
            i += 1
            j += 1
        if _at(left, i).isdigit():
            return 1
        if _at(right, j).isdigit():
            return -1
        if difference:
            return -1 if difference < 0 else 1
    return 0


def parse_depends(value):
    groups = []
    for part in value.split(","):
        alternatives = []
        for alternative in part.split("|"):
            match = DEPENDENCY_RE.match(alternative.strip())
            if match:
                alternatives.append(match.group(1))
        if alternatives:
            groups.append(tuple(alternatives))
    return tuple(groups)


def parse_provides(value):
    names = []
    for part in value.split(","):
        match = DEPENDENCY_RE.match(part.strip())
        if match:
            names.append(match.group(1))
    return tuple(names)


def _build(fields, feed):
    name = fields.get("Package", "")
    version = fields.get("Version", "")
    if not NAME_RE.match(name) or not version:
        return None
    filename = fields.get("Filename", "")
    if not FILENAME_RE.match(filename):
        filename = ""
    size_text = fields.get("Size", "")
    size = int(size_text) if size_text.isdigit() and len(size_text) < 12 else 0
    sha256 = fields.get("SHA256sum", "")
    if not SHA256_RE.match(sha256):
        sha256 = ""
    return Package(
        name=name,
        version=version,
        depends=parse_depends(fields.get("Depends", "")),
        provides=parse_provides(fields.get("Provides", "")),
        filename=filename,
        size=size,
        sha256=sha256.lower(),
        architecture=fields.get("Architecture", ""),
        section=fields.get("Section", ""),
        description=fields.get("Description", ""),
        feed=feed,
    )


def parse_packages(text, feed):
    packages = []
    fields = {}
    last = None
    for raw in text.split("\n") + [""]:
        line = raw.rstrip("\r")
        if not line.strip():
            if fields:
                package = _build(fields, feed)
                if package:
                    packages.append(package)
            fields = {}
            last = None
        elif line[0] in " \t":
            if last and len(fields[last]) < MAX_FIELD_LENGTH:
                fields[last] += "\n" + line.strip()
        else:
            key, separator, value = line.partition(":")
            if separator:
                last = key.strip()
                fields[last] = value.strip()[:MAX_FIELD_LENGTH]
    return packages


class Index:
    def __init__(self, packages):
        self.packages = list(packages)
        self.by_name = {}
        self.providers = {}
        for package in self.packages:
            self._keep(self.by_name, package.name, package)
            for alias in package.provides:
                self._keep(self.providers, alias, package)

    @staticmethod
    def _keep(table, key, package):
        current = table.get(key)
        if current is None or compare_versions(package.version, current.version) > 0:
            table[key] = package

    def __len__(self):
        return len(self.by_name)

    def lookup(self, name):
        return self.by_name.get(name) or self.providers.get(name)

    def is_virtual(self, name):
        return name in BUILTIN and self.lookup(name) is None

    def _choose(self, group, seen):
        first = None
        for alternative in group:
            package = self.lookup(alternative)
            if package is None:
                continue
            if package.name in seen:
                return package
            if first is None:
                first = package
        return first

    def resolve(self, names, with_dependencies=True):
        unknown = []
        missing = []
        ordered = []
        seen = set()
        queue = deque()
        for name in names:
            package = self.lookup(name)
            if package is None:
                unknown.append(name)
            elif package.name not in seen:
                seen.add(package.name)
                queue.append(package)
        while queue:
            package = queue.popleft()
            ordered.append(package)
            if not with_dependencies:
                continue
            for group in package.depends:
                chosen = self._choose(group, seen)
                if chosen is None:
                    if not BUILTIN.intersection(group):
                        missing.append((package.name, " | ".join(group)))
                elif chosen.name not in seen:
                    seen.add(chosen.name)
                    queue.append(chosen)
        return ordered, unknown, missing
