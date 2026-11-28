import ctypes
import os
import shlex

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import QWidget

from . import theme
from pakgrab.terminal import BANNER
from .bridge import Runner
from .palette import Palette
from .sidebar import SideBar
from .statusbar import StatusBar
from .topbar import TITLE, TOPBAR_HEIGHT, Scrim, TopBar
from .view import TerminalView

from pakgrab.commands import rate_text

SUBCOMMANDS = ("update", "dl", "list", "info", "feeds", "mirror", "help")
MIRROR_OPTIONS = ("all", "--feeds", "--arch", "--packages", "--no-deps", "--yes", "--dry-run")
SETTINGS = ("jobs", "limit-rate", "max-bandwidth", "resume")
SETTING_VALUES = {
    "jobs": ("1", "2", "4", "8", "16"),
    "limit-rate": ("off", "500K", "1M", "2M", "5M"),
    "max-bandwidth": ("0", "512", "1024", "2048", "4096"),
    "resume": ("on", "off"),
}
HINT = (
    theme.sgr(theme.DIM)
    + "Type "
    + theme.sgr(theme.GREEN)
    + "help"
    + theme.sgr(theme.DIM)
    + " or press "
    + theme.sgr(theme.ORANGE)
    + "Ctrl+P"
    + theme.sgr(theme.DIM)
    + " to open the command palette."
    + theme.RESET
)
BANNER_COLS = max(len(line) for line in BANNER)
SIDEBAR_WIDTH = theme.SIDEBAR_COLS * theme.CELL_W
COMPACT_WIDTH = SIDEBAR_WIDTH + 2 * theme.PAD_X + theme.SCROLL_WIDTH + BANNER_COLS * theme.CELL_W
DRAWER_MARGIN = 48
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def colorref(rgb):
    return ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | ((rgb >> 16) & 0xFF)


def dark_title_bar(window):
    if os.name != "nt":
        return
    try:
        handle = ctypes.c_void_p(int(window.winId()))
        setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
        for attribute, value in (
            (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (DWMWA_CAPTION_COLOR, colorref(theme.BG_BAR)),
            (DWMWA_BORDER_COLOR, colorref(theme.BG_BAR)),
            (DWMWA_TEXT_COLOR, colorref(theme.RULE)),
        ):
            setter(handle, attribute, ctypes.byref(ctypes.c_uint(value)), 4)
    except (AttributeError, OSError, ValueError):
        return


class MainWindow(QWidget):
    def __init__(self, config, keys_count, mirror, manager, shell, console, icon):
        super().__init__()
        self.config = config
        self.manager = manager
        self.shell = shell
        self.console = console
        self.name = manager.command
        self.runner = Runner(shell, console)
        self.shown = False
        self.compact = None
        self.drawer = False
        self.setWindowTitle(TITLE)
        if icon is not None and os.path.isfile(icon):
            self.setWindowIcon(QIcon(str(icon)))
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, theme.qcolor(theme.BG_MAIN))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.resize(1124, 668)
        self.setMinimumSize(400, 260)

        self.sidebar = SideBar(mirror.name or str(mirror), self)
        self.view = TerminalView(self)
        self.status = StatusBar(self._status_items(), self)
        self.palette = Palette(self._palette_items(), self.view)
        self.topbar = TopBar(self)
        self.scrim = Scrim(self)
        self.topbar.hide()
        self.scrim.hide()
        self.topbar.button.clicked.connect(self.toggle_drawer)
        self.scrim.clicked.connect(self.close_drawer)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_drawer)
        self.escape = QShortcut(QKeySequence("Esc"), self, activated=self.close_drawer)
        self.escape.setEnabled(False)

        self.sidebar.selected.connect(self._select_feed)
        self._sidebar_state()
        self.view.completer = self._complete
        self.view.submitted.connect(self._submitted)
        self.view.answered.connect(console.reply)
        self.view.interrupted.connect(self._interrupt)
        self.view.closed.connect(self.close)
        self.palette.chosen.connect(self._chosen)
        self.palette.dismissed.connect(self.view.setFocus)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.open_palette)
        manager.on_feed = console.feed

    def _status_items(self):
        config = self.shell.config
        return ("UTF-8", "%s %s" % (config.manager, config.release), config.arch)

    def _sidebar_state(self):
        shell = self.shell
        config = shell.config
        session = (
            ("Manager", config.manager),
            ("Arch", config.arch),
            ("Release", config.release),
            ("Feeds", str(len(config.feeds))),
            ("Keys", str(shell.keys_count)),
            ("Jobs", str(shell.settings.jobs)),
            ("Limit", rate_text(shell.settings.rate)),
        )
        self.sidebar.load(
            [path.name for path in shell.feed_files()],
            config.source.name,
            [feed.name for feed in config.feeds],
            session,
        )

    def _refresh(self):
        self.config = self.shell.config
        self.manager = self.shell.manager
        self.name = self.manager.command
        self._sidebar_state()
        self.status.set_parts(self._status_items())
        self.palette.items = self._palette_items()

    def _select_feed(self, name):
        self.close_drawer()
        if self.view.mode == "shell" and not self.status.busy:
            self.view.submit_text("feed use " + shlex.quote(name))

    def _palette_items(self):
        name = self.name
        return (
            ("%s update" % name, "Update package lists", "%s update" % name, True),
            ("%s dl" % name, "Download packages with dependencies", "%s dl " % name, False),
            ("%s list" % name, "List available packages", "%s list" % name, True),
            ("%s info" % name, "Show package details", "%s info " % name, False),
            ("%s feeds" % name, "Show configured feeds", "%s feeds" % name, True),
            ("%s mirror all" % name, "Download every package of every feed", "%s mirror all" % name, True),
            ("%s mirror --feeds" % name, "Mirror only selected feeds", "%s mirror --feeds " % name, False),
            ("%s mirror --arch" % name, "Mirror only selected architectures", "%s mirror --arch " % name, False),
            ("%s mirror --packages" % name, "Mirror only listed packages", "%s mirror --packages " % name, False),
            ("%s mirror --dry-run" % name, "Preview a selective mirror", "%s mirror --dry-run " % name, False),
            ("set jobs", "Set the number of parallel downloads", "set jobs ", False),
            ("set limit-rate", "Limit the total download speed", "set limit-rate ", False),
            ("set resume", "Turn partial download resume on or off", "set resume ", False),
            ("set", "Show transfer settings", "set", True),
            ("feed r", "Reload the feed file", "feed r", True),
            ("help", "Show built-in commands", "help", True),
            ("clear", "Clear the screen", "clear", True),
            ("exit", "Close the session", "exit", True),
        )

    def _complete(self, previous):
        if not previous:
            return [self.name, "feed", "clear", "help", "exit"]
        if previous == [self.name]:
            return list(SUBCOMMANDS)
        if previous == ["feed"]:
            return ["r", "ls", "use"]
        if previous == ["feed", "use"]:
            return list(self.sidebar.files)
        if previous == ["set"]:
            return list(SETTINGS)
        if len(previous) == 2 and previous[0] == "set":
            return list(SETTING_VALUES.get(previous[1], ()))
        if previous[:2] == [self.name, "mirror"]:
            if previous[-1] in ("--feeds", "-f"):
                return [feed.name for feed in self.shell.config.feeds]
            return list(MIRROR_OPTIONS)
        return []

    def showEvent(self, event):
        super().showEvent(event)
        if not self.shown:
            self.shown = True
            dark_title_bar(self)
            self.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()
        self.palette.reposition()

    def _arrange(self):
        width, height = self.width(), self.height()
        compact = width < COMPACT_WIDTH
        if compact != self.compact:
            self.compact = compact
            self.drawer = False
        status = theme.STATUS_HEIGHT
        top = TOPBAR_HEIGHT if compact else 0
        body = height - status - top
        self.status.setGeometry(0, height - status, width, status)
        self.topbar.setVisible(compact)
        self.topbar.setGeometry(0, 0, width, TOPBAR_HEIGHT)
        self.sidebar.overlay = compact
        if compact:
            side = min(SIDEBAR_WIDTH, width - DRAWER_MARGIN)
            self.sidebar.setFixedWidth(side)
            self.sidebar.setGeometry(0, top, side, body)
            self.view.setGeometry(0, top, width, body)
            self.scrim.setGeometry(0, top, width, body)
        else:
            self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
            self.sidebar.setGeometry(0, 0, SIDEBAR_WIDTH, body)
            self.view.setGeometry(SIDEBAR_WIDTH, 0, width - SIDEBAR_WIDTH, body)
        self._apply_drawer()

    def _apply_drawer(self):
        shown = self.compact and self.drawer
        self.sidebar.setVisible(shown or not self.compact)
        self.scrim.setVisible(shown)
        self.topbar.button.set_active(shown)
        self.escape.setEnabled(shown)
        if shown:
            self.scrim.raise_()
            self.sidebar.raise_()

    def toggle_drawer(self):
        if self.compact:
            self.drawer = not self.drawer
            self._apply_drawer()
            if not self.drawer:
                self.view.setFocus()

    def close_drawer(self):
        if self.drawer:
            self.drawer = False
            self._apply_drawer()
            self.view.setFocus()

    def start(self):
        self.view.append([HINT], clip=True)
        self.shell.show_banner()
        for warning in self.shell.warnings():
            self.console.write(theme.sgr(theme.YELLOW) + "Warning: " + warning + theme.RESET)
        self.pump()
        self.view.begin_input(self._prompt(), "shell")
        self.view.setFocus()

    @staticmethod
    def _prompt():
        return (("root@OpenWrt", theme.GREEN, False), (":~# ", theme.GREEN_SOFT, False))

    def pump(self):
        for op in self.console.drain():
            kind = op[0]
            if kind == "lines":
                self.view.append(op[1])
            elif kind == "banner":
                self.view.append(op[1], clip=True)
            elif kind == "live":
                self.view.set_live(op[1])
            elif kind == "clear":
                self.view.clear()
            elif kind == "feed":
                self.sidebar.set_feed(op[1], op[2])
            elif kind == "session":
                self._refresh()
            elif kind == "ask":
                self.view.begin_input(((op[1], None, False),), "ask")
            elif kind == "done":
                self._finished()

    def _submitted(self, line):
        if not line.strip():
            self.view.begin_input(self._prompt(), "shell")
            return
        self.status.set_busy(True)
        self.sidebar.set_locked(True)
        self.runner.start(line)

    def _finished(self):
        self.status.set_busy(False)
        self.sidebar.set_locked(False)
        self.view.set_live(None)
        if not self.shell.running:
            self.close()
            return
        self.view.begin_input(self._prompt(), "shell")
        self.view.setFocus()

    def _interrupt(self):
        if self.status.busy:
            self.console.reply("")
            self.runner.interrupt()

    def open_palette(self):
        if self.view.mode == "shell":
            self.palette.open_palette()

    def _chosen(self, text, run):
        self.view.setFocus()
        if self.view.mode != "shell":
            return
        if run:
            self.view.submit_text(text)
        else:
            self.view.set_text(text)

    def closeEvent(self, event):
        if self.status.busy:
            self.console.reply("")
            self.runner.interrupt()
        super().closeEvent(event)
