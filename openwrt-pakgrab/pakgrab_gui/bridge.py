import ctypes
import threading
from collections import deque

from pakgrab.terminal import PROGRESS_WIDTH, RESET_COLOR, SUCCESS_COLOR, Console, sanitize
from . import theme

RULE = theme.sgr(theme.RULE)
WHITE = theme.sgr(theme.TEXT)


def colorize_banner(text):
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and set(stripped) == {"-"}:
            lines.append(RULE + line + theme.RESET)
            continue
        lines.append(WHITE + line + theme.RESET)
    return lines


class GuiConsole(Console):
    def __init__(self, wake):
        self.stream = None
        self.width = 0
        self.vt = True
        self.colors = True
        self.busy = False
        self.frame = 0
        self.icons = "unicode"
        self.unicode = True
        self.wake = wake
        self.ops = deque()
        self.guard = threading.Lock()
        self.pending = False
        self.event = threading.Event()
        self.answer = ""

    def _post(self, *op):
        self.ops.append(op)
        with self.guard:
            if self.pending:
                return
            self.pending = True
        self.wake()

    def drain(self):
        with self.guard:
            self.pending = False
        ops = []
        while True:
            try:
                ops.append(self.ops.popleft())
            except IndexError:
                return ops

    def progress(self, text):
        text = sanitize(text)
        if len(text) > PROGRESS_WIDTH:
            text = text[:PROGRESS_WIDTH - 4] + "...]"
        self.busy = True
        self._post("live", [text])

    def progress_end(self):
        if self.busy:
            self.busy = False
            self._post("live", None)

    def can_live(self):
        return True

    def live(self, lines):
        self._post("live", list(lines))

    def live_clear(self):
        self._post("live", None)

    def cut(self, text):
        self.progress_end()
        self._post("lines", [str(text)])

    def line(self, text):
        self.progress_end()
        self._post("lines", [str(text)])

    def write(self, text=""):
        self.progress_end()
        self._post("lines", str(text).split("\n"))

    def success(self, text):
        self.progress_end()
        self._post("lines", [SUCCESS_COLOR + part + RESET_COLOR for part in str(text).split("\n")])

    def banner(self, text):
        self._post("banner", colorize_banner(text.rstrip("\n")))

    def clear(self):
        self._post("clear")

    def feed(self, name, ok):
        self._post("feed", name, ok)

    def done(self):
        self._post("done")

    def ask(self, prompt):
        self.answer = ""
        self.event.clear()
        self._post("ask", prompt)
        self.event.wait()
        return self.answer

    def reply(self, answer):
        self.answer = answer
        self.event.set()


class Runner:
    def __init__(self, shell, console):
        self.shell = shell
        self.console = console
        self.thread = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, line):
        self.thread = threading.Thread(target=self._run, args=(line,), daemon=True)
        self.thread.start()

    def _run(self, line):
        try:
            self.shell.run_line(line)
        except KeyboardInterrupt:
            self.console.write("^C")
        except Exception as error:
            self.console.write("-ash: %s" % error)
        finally:
            try:
                self.console.done()
            except KeyboardInterrupt:
                self.console.done()

    def interrupt(self):
        thread = self.thread
        if thread is not None and thread.is_alive():
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(KeyboardInterrupt))
