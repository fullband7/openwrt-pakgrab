import sys

from pakgrab.bootstrap import app_dir, assemble, build_parser, prepare
from pakgrab.config import ConfigError
from pakgrab.terminal import Console

BASE_DIR = app_dir(globals().get("__compiled__"), __file__)


def main(argv=None):
    args = build_parser(BASE_DIR, "OpenWrt package downloader").parse_args(argv)
    console = Console(width=max(0, min(args.width, 1000)))
    try:
        config, keys, manager_class, mirror = prepare(args, BASE_DIR)
    except ConfigError as error:
        sys.stderr.write("error: %s\n" % error)
        return 1
    manager, shell = assemble(config, manager_class, keys, mirror, args, console, input, BASE_DIR)
    if args.command is not None:
        return shell.run_line(args.command)
    shell.show_summary()
    shell.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
