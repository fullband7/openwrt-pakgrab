import os
import re
from pathlib import Path

LIST_SUFFIX = ".txt"
MAX_LIST_SIZE = 1024 * 1024
MAX_LIST_NAMES = 5000
SEPARATOR_RE = re.compile(r"[\s,;]+")


class ListError(Exception):
    pass


def is_list(argument):
    return argument.lower().endswith(LIST_SUFFIX)


def _match_name(candidate):
    wanted = candidate.name.lower()
    try:
        entries = list(os.scandir(candidate.parent))
    except OSError:
        return None
    for entry in sorted(entries, key=lambda item: item.name):
        if entry.name.lower() == wanted and entry.is_file():
            return Path(entry.path)
    return None


def locate(argument, directories):
    path = Path(argument).expanduser()
    candidates = [path] if path.is_absolute() else [Path(directory) / path for directory in directories]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for candidate in candidates:
        found = _match_name(candidate)
        if found is not None:
            return found
    raise ListError("%s: No such file" % argument)


def read(path):
    try:
        if path.stat().st_size > MAX_LIST_SIZE:
            raise ListError("%s: file too large" % path.name)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as error:
        raise ListError("cannot read %s: %s" % (path.name, error.strerror))
    names = []
    for line in text.splitlines():
        names.extend(token for token in SEPARATOR_RE.split(line.split("#", 1)[0]) if token)
    names = list(dict.fromkeys(names))
    if not names:
        raise ListError("%s: no packages found" % path.name)
    if len(names) > MAX_LIST_NAMES:
        raise ListError("%s: too many packages" % path.name)
    return names
