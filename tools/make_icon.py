"""Render the application icon to linger/assets/linger.ico (used by the packaged executable)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import linger  # noqa: F401
from PySide6.QtWidgets import QApplication

from linger import theme

app = QApplication([])
out = Path(__file__).resolve().parents[1] / 'linger' / 'assets' / 'linger.ico'
assert theme.app_icon(theme.DARK.accent).pixmap(256, 256).save(str(out), 'ICO'), 'ICO plugin unavailable'
print('saved', out)
