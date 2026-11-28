import os
import re
import shlex
import shutil
import sys
import textwrap
from pathlib import Path

from . import listfile
from .commands import (
    HelpRequested,
    UsageError,
    apply_setting,
    describe_settings,
    parse_mirror,
    rate_text,
)
from .config import ConfigError
from .fs import display
from .index import NAME_RE
from .selection import SelectionError

try:
    import readline
except ImportError:
    readline = None

PROMPT = "root@OpenWrt:~# "
CONFIRMATION = "Are you sure you want to mirror the entire repository? (y/n)"
MAX_LINE_LENGTH = 4096
MAX_ARGUMENTS = 64
MAX_NAME_ECHO = 64
SUCCESS_COLOR = "\033[38;2;0;179;89m"
RESET_COLOR = "\033[0m"

DEFAULT_WIDTH = 120
MIN_WIDTH = 20
PROGRESS_WIDTH = 48
VT_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
TMPF_TRUETYPE = 0x04
ICONS_UNICODE, ICONS_BASIC, ICONS_ASCII = "unicode", "basic", "ascii"
ICON_ENV = "PKG_ICONS"
MODERN_FONTS = ("cascadia", "jetbrains", "fira", "hack", "dejavu", "noto", "source code")
RICH_ENV_MARKERS = ("WT_SESSION", "ConEmuPID", "ALACRITTY_LOG", "WEZTERM_EXECUTABLE", "KITTY_WINDOW_ID")
DISTRIBUTION = "OpenWrt"
BANNER = (
    "",
    "  _______                     ________        __",
    " |       |.-----.-----.-----.|  |  |  |.----.|  |_",
    " |   -   ||  _  |  -__|     ||  |  |  ||   _||   _|",
    " |_______||   __|_____|__|__||________||__|  |____|",
    "          |__| W I R E L E S S   F R E E D O M",
    " -----------------------------------------------------",
    " %D %V",
    " -----------------------------------------------------",
)
SGR_RE = re.compile(r"(\x1b\[[0-9;]*m)")
PLACEHOLDER_RE = re.compile(r"%([DVA%])")

def usage_lines(name):
    return (
        "Usage: %s <command> [arguments]" % name,
        "",
        "Commands:",
        "  update                  Update the package lists and verify their signatures",
        "  dl [-n] <pkg|list.txt>  Download packages and their dependencies",
        "  list [pattern]          List available packages",
        "  info <pkg>              Show package details",
        "  feeds                   Show configured feeds",
        "  mirror all              Download every package of every feed",
        "  mirror [filters]        Download only the selected packages",
        "",
        "Options:",
        "  -n, --no-deps           Do not download dependencies",
        "",
        "Mirror filters:",
        "  -f, --feeds LIST        Only these feeds, e.g. base,luci",
        "  -a, --arch LIST         Only these architectures (arch independent packages are kept)",
        "  -p, --packages LIST     Only these packages or list files (name,name,list.txt)",
        "  -n, --no-deps           Do not add dependencies of the listed packages",
        "  -y, --yes               Do not ask for confirmation",
        "      --dry-run           Show what would be mirrored and exit",
    )


def mirror_lines(name):
    return (
        "Usage: %s mirror [all] [--feeds LIST] [--arch LIST] [--packages LIST] [-n] [-y] [--dry-run]" % name,
        "",
        "  -f, --feeds LIST        Only these feeds, e.g. base,luci (names or short names, wildcards allowed)",
        "  -a, --arch LIST         Only these architectures, arch independent packages are kept",
        "  -p, --packages LIST     Only these packages, comma separated names or .txt list files",
        "  -n, --no-deps           Do not add dependencies of the listed packages",
        "  -y, --yes               Do not ask for confirmation",
        "      --dry-run           Show what would be mirrored and exit",
    )


def set_lines():
    return (
        "Usage: set [jobs N | limit-rate RATE | max-bandwidth KBPS | resume on|off]",
        "",
        "  jobs N                  Parallel downloads (1-16)",
        "  limit-rate RATE         Total speed cap, e.g. 500K, 2M or off",
        "  max-bandwidth KBPS      Total speed cap in KiB/s, 0 for unlimited",
        "  resume on|off           Continue partial downloads",
    )


def feed_lines():
    return (
        "Usage: feed <command>",
        "",
        "Commands:",
        "  r, refresh              Reload the feed file",
        "  ls                      List the available feed files",
        "  use <file>              Switch to another feed file",
    )


def same_file(left, right):
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def help_lines(name):
    return (
        "Built-in commands:",
        "  %-8s package downloader" % name,
        "  feed     reload or switch the feed file",
        "  set      show or change jobs, speed limit and resume",
        "  clear    clear the screen",
        "  help     show this help",
        "  exit     close the session",
    )


def sanitize(text):
    return "".join(char if char.isprintable() or char == "\t" else "?" for char in text)


def enable_vt(stream):
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & VT_PROCESSING:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | VT_PROCESSING))
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def console_font():
    try:
        import ctypes
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = (("X", ctypes.c_short), ("Y", ctypes.c_short))

        class FONTINFO(ctypes.Structure):
            _fields_ = (
                ("cbSize", wintypes.ULONG),
                ("nFont", wintypes.DWORD),
                ("dwFontSize", COORD),
                ("FontFamily", wintypes.UINT),
                ("FontWeight", wintypes.UINT),
                ("FaceName", wintypes.WCHAR * 32),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetCurrentConsoleFontEx.argtypes = (wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(FONTINFO))
        kernel32.GetCurrentConsoleFontEx.restype = wintypes.BOOL
        info = FONTINFO()
        info.cbSize = ctypes.sizeof(FONTINFO)
        if not kernel32.GetCurrentConsoleFontEx(kernel32.GetStdHandle(STD_OUTPUT_HANDLE), False, ctypes.byref(info)):
            return None
        return info.FaceName, bool(info.FontFamily & TMPF_TRUETYPE)
    except (AttributeError, ImportError, OSError, ValueError):
        return None


def windows_icons(environ=None, font=console_font):
    environ = os.environ if environ is None else environ
    if any(name in environ for name in RICH_ENV_MARKERS):
        return ICONS_UNICODE
    if environ.get("TERM_PROGRAM") == "vscode" or "JetBrains" in environ.get("TERMINAL_EMULATOR", ""):
        return ICONS_UNICODE
    probe = font()
    if probe is None:
        return ICONS_ASCII
    face, truetype = probe
    if not truetype:
        return ICONS_ASCII
    if any(name in face.lower() for name in MODERN_FONTS):
        return ICONS_UNICODE
    return ICONS_BASIC


def render_banner(text, values):
    return PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], text)


class Console:
    def __init__(self, stream=None, width=0):
        self.stream = stream or sys.stdout
        self.width = width
        self.vt = self.stream.isatty() and enable_vt(self.stream)
        self.colors = self.vt and "NO_COLOR" not in os.environ
        self.busy = False
        self.frame = 0
        self.icons = self._detect_icons()
        self.unicode = self.icons == ICONS_UNICODE

    def _encodes(self, text):
        try:
            text.encode(getattr(self.stream, "encoding", None) or "ascii")
        except (UnicodeEncodeError, LookupError):
            return False
        return True

    def _detect_icons(self):
        forced = os.environ.get(ICON_ENV, "").strip().lower()
        if forced in (ICONS_UNICODE, ICONS_BASIC, ICONS_ASCII):
            return forced
        if self._encodes("\u2588\u2800\u2193"):
            level = ICONS_UNICODE
        elif self._encodes("\u2588\u2591\u2193"):
            level = ICONS_BASIC
        else:
            return ICONS_ASCII
        if os.name == "nt":
            cap = windows_icons() if self.vt else ICONS_ASCII
        elif os.environ.get("TERM") == "linux":
            cap = ICONS_BASIC
        else:
            cap = ICONS_UNICODE
        order = (ICONS_ASCII, ICONS_BASIC, ICONS_UNICODE)
        return min(level, cap, key=order.index)

    def paint(self, text, code):
        return "\033[%sm%s\033[0m" % (code, text) if self.colors else text

    def _fit(self, line, width):
        out = []
        used = 0
        for position, part in enumerate(SGR_RE.split(line)):
            if position % 2:
                if self.colors:
                    out.append(part)
                continue
            part = sanitize(part)[:max(0, width - used)]
            used += len(part)
            out.append(part)
        return "".join(out)

    def columns(self):
        if self.width > 0:
            return max(MIN_WIDTH, self.width)
        columns = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
        if os.name == "nt":
            columns -= 1
        return max(MIN_WIDTH, columns)

    def _lines(self, text):
        width = self.columns()
        for part in str(text).split("\n"):
            part = sanitize(part)
            if len(part) <= width:
                yield part
                continue
            yield from textwrap.wrap(
                part,
                width,
                expand_tabs=False,
                replace_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )

    def progress(self, text):
        if not self.vt:
            return
        text = sanitize(text)
        if len(text) > PROGRESS_WIDTH:
            text = text[:PROGRESS_WIDTH - 4] + "...]"
        self.stream.write("\r\033[K" + text)
        self.stream.flush()
        self.busy = True

    def progress_end(self):
        if self.busy:
            self.stream.write("\r\033[K")
            self.stream.flush()
            self.busy = False

    def can_live(self):
        return self.vt

    def live(self, lines):
        width = self.columns()
        out = []
        if self.frame:
            out.append("\033[%dA\r" % self.frame)
        else:
            out.append("\033[?25l")
        for line in lines:
            out.append(self._fit(line, width) + "\033[0m\033[K\n")
        out.append("\033[J")
        self.frame = len(lines)
        self.stream.write("".join(out))
        self.stream.flush()

    def live_clear(self):
        if not self.frame:
            return
        self.stream.write("\033[%dA\r\033[J\033[?25h" % self.frame)
        self.stream.flush()
        self.frame = 0

    def cut(self, text):
        self.progress_end()
        text = sanitize(text)
        width = self.columns()
        if len(text) > width:
            text = text[:width - 1] + "]"
        self.stream.write(text + "\n")
        self.stream.flush()

    def line(self, text):
        self.progress_end()
        self.stream.write(sanitize(text) + "\n")
        self.stream.flush()

    def write(self, text=""):
        self.progress_end()
        for line in self._lines(text):
            self.stream.write(line + "\n")
        self.stream.flush()

    def banner(self, text):
        colors = self.colors
        parts = SGR_RE.split(text)
        styled = False
        for position in range(len(parts)):
            if position % 2:
                if colors:
                    styled = True
                else:
                    parts[position] = ""
            else:
                parts[position] = "".join(
                    char if char.isprintable() or char in "\t\n" else "?" for char in parts[position]
                )
        text = "".join(parts).rstrip("\n")
        if styled:
            text += RESET_COLOR
        self.stream.write(text + "\n")
        self.stream.flush()

    def success(self, text):
        self.progress_end()
        for line in self._lines(text):
            if self.colors:
                line = SUCCESS_COLOR + line + RESET_COLOR
            self.stream.write(line + "\n")
        self.stream.flush()

    def session(self):
        return

    def clear(self):
        if self.vt:
            self.stream.write("\033[H\033[2J")
            self.stream.flush()
        elif os.name == "nt" and self.stream.isatty():
            os.system("cls")


class Shell:
    def __init__(self, config, manager, console, keys_count, mirror, ask=input, loader=None):
        self.ask = ask
        self.config = config
        self.manager = manager
        self.console = console
        self.keys_count = keys_count
        self.mirror = mirror
        self.loader = loader
        self.running = True
        self._bind()

    def _bind(self):
        self.name = self.manager.command
        self.commands = {
            self.name: self._package,
            "help": self._help,
            "clear": self._clear,
            "exit": self._exit,
            "logout": self._exit,
        }
        self.commands["set"] = self._set
        if self.loader is not None:
            self.commands["feed"] = self._feed

    @property
    def settings(self):
        return self.manager.settings

    def show_summary(self):
        self.show_details()
        self.show_banner()

    def show_details(self):
        write = self.console.write
        write("Package Manager : " + self.config.manager)
        write("Architecture    : " + self.config.arch)
        write("Release         : " + self.config.release)
        write("Feeds           : %d (%s)" % (len(self.config.feeds), self.config.source.name))
        write("Trusted keys    : %d" % self.keys_count)
        write("Mirror          : " + str(self.mirror))
        write("Transfers       : %d jobs, limit %s, resume %s" % (self.settings.jobs, rate_text(self.settings.rate), "on" if self.settings.resume else "off"))
        for warning in self.warnings():
            write("Warning         : " + warning)

    def warnings(self):
        items = list(self.config.warnings)
        if not self.keys_count:
            kind = ".pem public keys" if self.config.manager == "APK" else "usign public keys"
            items.append("no trusted keys loaded, add %s to the keys folder" % kind)
        return items

    def show_banner(self):
        values = {"D": DISTRIBUTION, "V": self.config.release, "A": self.config.arch, "%": "%"}
        self.console.banner(render_banner("\n".join(BANNER), values))

    def run_line(self, line):
        if len(line) > MAX_LINE_LENGTH:
            self.console.write("-ash: line too long")
            return 1
        try:
            argv = shlex.split(line)
        except ValueError:
            self.console.write("-ash: syntax error: unterminated quoted string")
            return 2
        if not argv:
            return 0
        if len(argv) > MAX_ARGUMENTS:
            self.console.write("-ash: too many arguments")
            return 1
        handler = self.commands.get(argv[0])
        if handler is None:
            self.console.write("-ash: %s: not found" % argv[0][:MAX_NAME_ECHO])
            return 127
        return handler(argv[1:])

    def loop(self):
        if readline is not None:
            readline.set_auto_history(True)
        while self.running:
            try:
                line = input(PROMPT)
            except EOFError:
                self.console.write("logout")
                break
            except KeyboardInterrupt:
                self.console.write("^C")
                continue
            try:
                self.run_line(line)
            except KeyboardInterrupt:
                self.console.write("^C")

    def _help(self, args):
        for line in help_lines(self.name):
            self.console.write(line)
        return 0

    def _clear(self, args):
        self.console.clear()
        return 0

    def _exit(self, args):
        self.running = False
        return 0

    def _package(self, args):
        if not args or args[0] in ("help", "-h", "--help"):
            for line in usage_lines(self.name):
                self.console.write(line)
            return 0
        command, rest = args[0], args[1:]
        if command == "update":
            return self.manager.update()
        if command in ("dl", "download"):
            return self._download(rest)
        if command in ("list", "search"):
            if len(rest) > 1:
                self.console.write("Usage: %s list [pattern]" % self.name)
                return 1
            return self.manager.list_packages(rest[0] if rest else None)
        if command == "info":
            if len(rest) != 1:
                self.console.write("Usage: %s info <pkg>" % self.name)
                return 1
            return self._info(rest[0])
        if command == "feeds":
            return self.manager.feeds()
        if command == "mirror":
            return self._mirror(rest)
        self.console.write("%s: unknown command '%s'" % (self.name, command[:MAX_NAME_ECHO]))
        return 1

    def feed_files(self):
        files = list(self.loader.files())
        source = self.config.source
        if not any(same_file(path, source) for path in files):
            files.insert(0, source)
        return files

    def _feed_path(self, argument):
        files = self.feed_files()
        for path in files:
            if path.name == argument:
                return path
        for path in files:
            if path.name.lower() == argument.lower():
                return path
        return self.loader.resolve(argument)

    def switch(self, path):
        try:
            config, keys_count, manager = self.loader.load(path)
        except ConfigError as error:
            self.console.write("feed: %s" % error)
            return 1
        manager.on_feed = self.manager.on_feed
        self.config = config
        self.manager = manager
        self.keys_count = keys_count
        self._bind()
        self.show_details()
        self.console.session()
        return 0

    def _feed(self, args):
        if not args or args[0] in ("help", "-h", "--help"):
            for line in feed_lines():
                self.console.write(line)
            return 0
        command, rest = args[0], args[1:]
        if command in ("r", "refresh") and not rest:
            return self.switch(self.config.source)
        if command == "ls" and not rest:
            for path in self.feed_files():
                self.console.write("%s %s" % ("*" if same_file(path, self.config.source) else " ", path.name))
            return 0
        if command == "use" and len(rest) == 1:
            return self.switch(self._feed_path(rest[0]))
        self.console.write("Usage: feed r | ls | use <file>")
        return 1

    def _confirm(self, question):
        try:
            answer = self.ask(question + " ")
        except EOFError:
            answer = ""
        return answer.strip() in ("y", "Y")

    def _directories(self):
        return list(dict.fromkeys((Path.cwd(), self.config.source.parent)))

    def _mirror(self, args):
        try:
            options = parse_mirror(args, self._directories())
        except HelpRequested:
            for line in mirror_lines(self.name):
                self.console.write(line)
            return 0
        except UsageError as error:
            self.console.write("%s mirror: %s" % (self.name, error))
            self.console.write("Try '%s mirror --help'." % self.name)
            return 1
        selection = options.selection
        if not options.dry_run and not selection.active and not options.assume_yes:
            if not self._confirm(CONFIRMATION):
                self.console.write("Mirror cancelled.")
                return 1
        job = self.manager.mirror_job(selection)
        try:
            plan = job.prepare()
        except SelectionError as error:
            self.console.write("%s mirror: %s" % (self.name, error))
            return 1
        if plan is None:
            return 1
        job.describe(plan)
        if options.dry_run:
            return 1 if plan.unknown else 0
        if not plan.count:
            self.console.write("No packages match the selection.")
            return 1
        if selection.active and not options.assume_yes and not self._confirm(job.question(plan)):
            self.console.write("Mirror cancelled.")
            return 1
        return job.run(plan)

    def _set(self, args):
        if not args:
            for line in describe_settings(self.settings):
                self.console.write(line)
            return 0
        if args[0] in ("help", "-h", "--help"):
            for line in set_lines():
                self.console.write(line)
            return 0
        if len(args) != 2:
            self.console.write("Usage: set <name> <value>")
            return 1
        try:
            apply_setting(self.settings, args[0], args[1])
        except UsageError as error:
            self.console.write("set: %s" % error)
            return 1
        self.console.session()
        for line in describe_settings(self.settings):
            self.console.write(line)
        return 0

    def _info(self, name):
        if not NAME_RE.match(name):
            self.console.write("Unknown package '%s'." % name[:MAX_NAME_ECHO])
            return 1
        return self.manager.info(name)

    def _download(self, args):
        with_dependencies = True
        names = []
        lists = []
        options_done = False
        for arg in args:
            if not options_done and arg == "--":
                options_done = True
            elif not options_done and arg in ("-n", "--no-deps"):
                with_dependencies = False
            elif not options_done and arg.startswith("-"):
                self.console.write("%s: unknown option '%s'" % (self.name, arg[:MAX_NAME_ECHO]))
                return 1
            elif listfile.is_list(arg):
                lists.append(arg)
            else:
                names.append(arg)
        if not names and not lists:
            self.console.write("Usage: %s dl [-n] <pkg|list.txt> [...]" % self.name)
            return 1
        for name in names:
            if not NAME_RE.match(name):
                self.console.write("Unknown package '%s'." % name[:MAX_NAME_ECHO])
                return 1
        if not lists:
            return self.manager.download(list(dict.fromkeys(names)), with_dependencies)
        return self._download_lists(names, lists, with_dependencies)

    def _download_lists(self, names, lists, with_dependencies):
        directories = list(dict.fromkeys((Path.cwd(), self.config.source.parent)))
        collected = list(names)
        invalid = False
        for argument in lists:
            try:
                path = listfile.locate(argument, directories)
                entries = listfile.read(path)
            except listfile.ListError as error:
                self.console.write("%s: %s" % (self.name, error))
                return 1
            self.console.write("Read %d packages from %s" % (len(entries), display(path)))
            for entry in entries:
                if NAME_RE.match(entry):
                    collected.append(entry)
                else:
                    invalid = True
                    self.console.write("Invalid package name '%s'." % entry[:MAX_NAME_ECHO])
        collected = list(dict.fromkeys(collected))
        if not collected:
            return 1
        status = self.manager.download(collected, with_dependencies, True)
        return 1 if invalid else status
