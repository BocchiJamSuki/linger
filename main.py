import sys

import linger  # noqa: F401  (Windows DLL preload must run before Qt is imported)
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from linger.window import Window

INSTANCE = 'Linger.SingleInstance'


def already_running():
    """Ask a running copy to show itself. Two copies would bill twice and fight over the hotkey."""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE)
    if not socket.waitForConnected(300):
        return False
    socket.write(b'show')
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def main():
    if sys.platform == 'win32':
        import ctypes
        try:  # Own taskbar identity, so Windows shows our icon rather than Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Linger.Linger')
        except Exception:
            pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationDisplayName('Linger')
    app.setStyle('Fusion')
    if already_running():
        return 0
    window = Window()
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE)
    server.listen(INSTANCE)
    server.newConnection.connect(window.bring_to_front)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
