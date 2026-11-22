import sys

from pakgrab.bootstrap import app_dir, assemble, build_parser, prepare
from pakgrab.config import ConfigError

try:
    from pakgrab_gui import app as gui
except ImportError:
    gui = None

BASE_DIR = app_dir(globals().get("__compiled__"), __file__)


def main(argv=None):
    args = build_parser(BASE_DIR, "OpenWrt PakGrab", terminal=False).parse_args(argv)
    if gui is None:
        sys.stderr.write("error: PySide6 is required, install it with: pip install PySide6-Essentials\n")
        return 1
    try:
        config, keys, manager_class, mirror = prepare(args, BASE_DIR)
    except ConfigError as error:
        gui.alert(str(error))
        return 1

    def factory(console, ask):
        return assemble(config, manager_class, keys, mirror, args, console, ask, BASE_DIR)

    return gui.launch(config, len(keys), mirror, factory, BASE_DIR / "pkg.ico")


if __name__ == "__main__":
    sys.exit(main())
