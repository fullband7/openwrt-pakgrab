import os
import tempfile
from pathlib import Path


class PathError(Exception):
    pass


def safe_join(root, *parts):
    base = Path(root).resolve()
    for part in parts:
        if not part or part in (".", "..") or "/" in part or "\\" in part:
            raise PathError("path escapes the mirror directory")
    if not parts:
        return base
    target = base.joinpath(*parts)
    parent = target.parent.resolve()
    if parent != base and base not in parent.parents:
        raise PathError("path escapes the mirror directory")
    return parent / target.name


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def display(path):
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return str(path)
