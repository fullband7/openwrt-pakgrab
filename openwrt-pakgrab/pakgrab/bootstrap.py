import argparse
import sys
from pathlib import Path

from . import ecdsa, usign
from .apkmanager import ApkManager
from .config import discover
from .manager import Manager
from .net import Fetcher
from .terminal import Shell


def app_dir(compiled, entry):
    if compiled is not None:
        directory = getattr(compiled, "containing_dir", None)
        return Path(directory or Path(sys.argv[0]).resolve().parent).resolve()
    return Path(entry).resolve().parent


def build_parser(base_dir, description, terminal=True):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("-c", "--config", help="path to a distfeeds file")
    parser.add_argument("-d", "--dir", default=str(base_dir / "repo"), help="mirror output directory")
    parser.add_argument("-k", "--keys", default=str(base_dir / "keys"), help="directory with public keys (usign files or .pem)")
    parser.add_argument("--allow-http", action="store_true", help="permit plain http feeds")
    parser.add_argument("--timeout", type=int, default=30, help="network timeout in seconds")
    if terminal:
        parser.add_argument("-e", "--exec", dest="command", help="run a single command and exit")
        parser.add_argument("-w", "--width", type=int, default=0, help="output line width, 0 detects the terminal")
    return parser


def prepare(args, base_dir):
    config = discover([base_dir, Path.cwd()], args.config)
    if config.manager == "APK":
        keys, _ = ecdsa.load_keys(args.keys)
        manager_class = ApkManager
    else:
        keys, _ = usign.load_keys(args.keys)
        manager_class = Manager
    return config, keys, manager_class, Path(args.dir).resolve()


def assemble(config, manager_class, keys, mirror, args, console, ask):
    fetcher = Fetcher(allow_http=args.allow_http, timeout=max(1, min(args.timeout, 300)))
    manager = manager_class(config, mirror, keys, fetcher, console)
    return manager, Shell(config, manager, console, len(keys), mirror, ask)
