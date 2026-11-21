import os
from collections import namedtuple
from functools import cmp_to_key
from pathlib import Path

from .index import compare_versions

DOWNLOADED = "downloaded"
UPDATED = "updated"
CURRENT = "current"

Outcome = namedtuple("Outcome", "status destination url previous")


def identify_ipk(filename, names):
    if not filename.endswith(".ipk"):
        return None
    stem = filename[:-4]
    position = len(stem)
    fallback = None
    while True:
        position = stem.rfind("_", 0, position)
        if position <= 0:
            return fallback
        name = stem[:position]
        if name not in names:
            continue
        version = stem[position + 1:].split("_", 1)[0]
        if version[:1].isdigit():
            return name, version
        if fallback is None and version:
            fallback = (name, version)


def identify_apk(filename, names):
    if not filename.endswith(".apk"):
        return None
    stem = filename[:-4]
    position = len(stem)
    while True:
        position = stem.rfind("-", 0, position)
        if position <= 0:
            return None
        if stem[position + 1:position + 2].isdigit() and stem[:position] in names:
            return stem[:position], stem[position + 1:]


def scan(directory, identify, names):
    found = {}
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return found
    for entry in entries:
        identity = identify(entry.name, names)
        if identity and entry.is_file(follow_symlinks=False):
            found.setdefault(identity[0], []).append((identity[1], Path(entry.path)))
    return found


def compare(left, right):
    return 0 if left == right else compare_versions(left, right)


def newest(versions):
    return max(versions, key=cmp_to_key(compare))
