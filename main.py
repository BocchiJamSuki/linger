import sys

import linger  # noqa: F401  (Windows DLL preload must run before Qt is imported)
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from linger.window import Window


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
    window = Window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
