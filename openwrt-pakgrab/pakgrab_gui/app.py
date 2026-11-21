import os
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from .bridge import GuiConsole
from .topbar import TITLE
from .window import MainWindow


class Waker(QObject):
    fired = Signal()


def application():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    return app


def alert(message):
    application()
    box = QMessageBox(QMessageBox.Icon.Critical, TITLE, message)
    box.exec()


def launch(config, keys_count, mirror, factory, icon):
    app = application()
    waker = Waker()
    console = GuiConsole(waker.fired.emit)
    manager, shell = factory(console, console.ask)
    window = MainWindow(config, keys_count, mirror, manager, shell, console, icon)
    waker.fired.connect(window.pump, Qt.ConnectionType.QueuedConnection)
    window.show()
    code = app.exec()
    if window.runner.busy:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.flush()
        os._exit(code)
    return code
