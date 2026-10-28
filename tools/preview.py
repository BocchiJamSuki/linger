"""Render the real window with sample text into docs/. Never captures audio or connects."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import linger  # noqa: F401
from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from linger.audio import Source
from linger.engine import LIVE
from linger.transcript import FINAL, GAP
from linger.window import Window

SPEECH = [
    (0, "Thank you for joining us today. So first, could you briefly introduce yourself"),
    (4, "and tell us why you decided to apply to our program?"),
    (31, "I see. That's very interesting."),
    (34, "Now, in your research proposal, you mentioned the reinforcement learning approach,"),
    (39, "I mean, specifically the model-based one."),
    (41, "How would you handle the sample efficiency problem in a real environment?"),
]


class Alive:
    def is_alive(self):
        return True

    def stop(self):
        pass


def render(mode, out):
    tmp = Path(tempfile.mkdtemp())
    settings = QSettings(str(tmp / 'preview.ini'), QSettings.Format.IniFormat)
    settings.setValue('theme', mode)
    sources = [Source('', '系统默认 · 扬声器 (Realtek(R) Audio)'), Source('x', '扬声器 (Realtek(R) Audio)')]
    w = Window(settings=settings, sources=lambda: sources, hotkeys=False, records=tmp / 'records')
    w.apply_hotkey('Ctrl+`')
    w.resize(940, 720)
    w.show()
    base = 1_800_000_000
    for offset, text in SPEECH:
        seg = w.view.push_segment(FINAL, text)
        seg.start = seg.end = base + offset
    w.view.origin = base
    gap = w.view.push_segment(GAP, '连接中断约 2 秒 · 已自动恢复')
    seg = w.view.push_segment(FINAL, 'Sorry, could you repeat the last part?')
    seg.start = seg.end = base + 58
    w.view.push_partial('Yes, so what I was asking is whether the simulation results would')
    w.view.flush()
    w.worker, w.mode = Alive(), 'live'
    w._update_controls()
    w.set_status('正在实时转写', LIVE)
    w.saved.setText('已自动保存 · 2026-10-29 1403.txt')
    w.saved.show()
    w.meter.set_peak(0.2)
    for _ in range(30):
        w.meter.advance()
    w.view.picked = {3, 4}
    w.view.anchor = 3
    w.view._update_floating()
    app.processEvents()
    w.view.viewport().update()
    app.processEvents()
    w.grab().save(str(out))
    w.worker = None
    w.hide()


app = QApplication([])
app.setStyle('Fusion')
folder = Path(__file__).resolve().parents[1] / 'docs'
folder.mkdir(exist_ok=True)
for mode in ('dark', 'light'):
    render(mode, folder / f'ui-{mode}.png')
print('saved to', folder)
