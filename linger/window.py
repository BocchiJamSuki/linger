from datetime import datetime
import os
from pathlib import Path
import time

from PySide6.QtCore import QPoint, QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from . import hotkey, theme
from .audio import list_sources
from .engine import BUSY, COMMIT, ERROR, GAP as GAP_EVENT, IDLE, LIVE, OK, PROVISIONAL as PROVISIONAL_EVENT, WARN
from .engine import Engine, Mailbox
from .theme import Glyph
from .transcript import FINAL, GAP, PROVISIONAL, RESUME, Recorder
from .view import TranscriptView
from .widgets import LevelMeter, StatusPill

FONT_SIZES = (15, 16, 17, 18, 19, 20, 22, 24, 26, 28, 32)
DEFAULT_FONT_SIZE = 19
HOTKEY_REPEAT = 2.0  # Seconds within which another press reaches one segment further back.


def records_folder():
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return Path(base) / 'Linger'


class SourceBox(QComboBox):
    """Re-scans speakers every time it opens, so a newly plugged device just appears."""

    def __init__(self, refresh):
        super().__init__()
        self.refresh = refresh

    def showPopup(self):
        self.refresh()
        super().showPopup()


class Window(QMainWindow):
    def __init__(self, *, engine_factory=Engine, settings=None, sources=list_sources, hotkeys=True, records=None):
        super().__init__()
        self.engine_factory = engine_factory
        self.sources = sources
        self.settings = settings if settings is not None else QSettings('Linger', 'Linger')
        self.recorder = Recorder(records or records_folder())
        self.worker = self.mailbox = None
        self.mode = None
        self.stopping = self.closing = False
        self.tone = IDLE
        self.colors = theme.DARK
        self.hotkey_presses = (0.0, 0)
        self.font_size = int(self.settings.value('font/size', DEFAULT_FONT_SIZE))
        self.pinned = self.settings.value('window/pinned', False, type=bool)

        self.setWindowTitle('Linger')
        self.setMinimumSize(560, 420)
        self.resize(940, 720)
        self._build()
        self._build_menu()
        if self.pinned:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        geometry = self.settings.value('window/geometry')
        if geometry is not None:
            self.restoreGeometry(geometry)

        self.hotkey = hotkey.GlobalHotkey(QApplication.instance(), self.copy_recent) if hotkeys else None
        self.apply_hotkey(self.settings.value('hotkey', hotkey.DEFAULT))
        self.apply_theme()
        self.apply_font_size()
        self.set_status('准备就绪。点「开始」才会连接 ElevenLabs 并计费。')
        self.refresh_sources()  # May replace the status with a device problem.

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(lambda: self.toast.setText(''))

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self):
        root = QWidget()
        root.setObjectName('root')
        page = QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        top = QFrame()
        top.setObjectName('topbar')
        bar = QHBoxLayout(top)
        bar.setContentsMargins(16, 10, 12, 10)
        bar.setSpacing(6)
        self.mark = QLabel()
        bar.addWidget(self.mark)
        title = QLabel('Linger')
        title.setObjectName('appTitle')
        bar.addWidget(title)
        bar.addSpacing(10)
        self.source = SourceBox(self.refresh_sources)
        self.source.setObjectName('source')
        self.source.setToolTip('要转写的声音来源：录下这个扬声器正在播放的内容')
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.source.setMaximumWidth(360)
        self.source.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.source.setItemDelegate(QStyledItemDelegate(self.source))  # Honours QSS ::item rules.
        popup = self.source.view().window()  # Translucent so rounded corners have no square backing.
        popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.FramelessWindowHint
                             | Qt.WindowType.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if isinstance(popup, QFrame):
            popup.setFrameShape(QFrame.Shape.NoFrame)
        self.source.currentIndexChanged.connect(self._source_chosen)
        bar.addWidget(self.source)
        bar.addStretch(1)
        self.pill = StatusPill()
        bar.addWidget(self.pill)
        bar.addSpacing(4)
        self.pin_button = self._icon_button('窗口置顶')
        self.pin_button.setCheckable(True)
        self.pin_button.clicked.connect(self.toggle_pin)
        self.more_button = self._icon_button('更多')
        bar.addWidget(self.pin_button)
        bar.addWidget(self.more_button)
        bar.addSpacing(4)
        self.primary = QPushButton()
        self.primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary.setMinimumWidth(84)
        self.primary.setIconSize(QSize(12, 12))
        self.primary.clicked.connect(self.toggle)
        bar.addWidget(self.primary)
        page.addWidget(top)

        self.view = TranscriptView()
        self.view.on_copy = lambda n: self.flash(f'已复制 {n} 段')
        page.addWidget(self.view, 1)

        status = QFrame()
        status.setObjectName('statusbar')
        row = QHBoxLayout(status)
        row.setContentsMargins(16, 6, 16, 7)
        row.setSpacing(10)
        self.meter = LevelMeter()
        self.meter.setToolTip('输入电平')
        row.addWidget(self.meter)
        self.status = QLabel()
        self.status.setObjectName('statusText')
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.status, 1)
        self.toast = QLabel()
        self.toast.setObjectName('toast')
        row.addWidget(self.toast)
        self.saved = QPushButton()
        self.saved.setObjectName('note')
        self.saved.setFlat(True)
        self.saved.setCursor(Qt.CursorShape.PointingHandCursor)
        self.saved.setStyleSheet('QPushButton { background: transparent; border: none; padding: 0; min-height: 0; }')
        self.saved.setToolTip('打开记录文件夹')
        self.saved.clicked.connect(self.open_records)
        self.saved.hide()
        row.addWidget(self.saved)
        self.hotkey_note = QLabel()
        self.hotkey_note.setObjectName('note')
        row.addWidget(self.hotkey_note)
        page.addWidget(status)
        self.setCentralWidget(root)

        QShortcut(QKeySequence('Ctrl+='), self, lambda: self.step_font(1))
        QShortcut(QKeySequence.StandardKey.ZoomIn, self, lambda: self.step_font(1))
        QShortcut(QKeySequence.StandardKey.ZoomOut, self, lambda: self.step_font(-1))

    def _icon_button(self, tip):
        b = QPushButton()
        b.setProperty('kind', 'icon')
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _build_menu(self):
        menu = QMenu(self)
        menu.addAction('放大字号', lambda: self.step_font(1)).setShortcut(QKeySequence('Ctrl+='))
        menu.addAction('缩小字号', lambda: self.step_font(-1)).setShortcut(QKeySequence('Ctrl+-'))
        menu.addSeparator()

        keys = menu.addMenu('全局复制快捷键')
        self.hotkey_actions = QActionGroup(self)
        for combo in [*hotkey.COMBOS, None]:
            action = QAction(combo or '关闭', self, checkable=True)
            action.setData(combo)
            action.triggered.connect(lambda _=False, c=combo: self.apply_hotkey(c))
            self.hotkey_actions.addAction(action)
            keys.addAction(action)

        looks = menu.addMenu('外观')
        self.theme_actions = QActionGroup(self)
        for mode, text in (('system', '跟随系统'), ('light', '浅色'), ('dark', '深色')):
            action = QAction(text, self, checkable=True)
            action.setData(mode)
            action.setChecked(self.settings.value('theme', 'system') == mode)
            action.triggered.connect(lambda _=False, m=mode: self.set_theme(m))
            self.theme_actions.addAction(action)
            looks.addAction(action)

        menu.addSeparator()
        self.selftest_action = menu.addAction('运行自检（音频 + API）', lambda: self.start('selftest'))
        menu.addAction('复制全部内容', self.copy_all)
        menu.addAction('打开记录文件夹', self.open_records)
        self.new_action = menu.addAction('新建记录（清空屏幕）', self.new_transcript)
        self.menu = menu
        self.more_button.clicked.connect(self.show_menu)

    def show_menu(self):
        # Right-aligned under the button, so the menu never hangs off the window's edge.
        corner = self.more_button.mapToGlobal(self.more_button.rect().bottomRight())
        self.menu.popup(corner + QPoint(-self.menu.sizeHint().width(), 6))

    # ── appearance ────────────────────────────────────────────────────────────
    def is_dark(self):
        mode = self.settings.value('theme', 'system')
        if mode in ('dark', 'light'):
            return mode == 'dark'
        hints = QApplication.styleHints()
        return not hasattr(hints, 'colorScheme') or hints.colorScheme() != Qt.ColorScheme.Light

    def set_theme(self, mode):
        self.settings.setValue('theme', mode)
        self.apply_theme()

    def apply_theme(self):
        c = self.colors = theme.DARK if self.is_dark() else theme.LIGHT
        self.setPalette(theme.qpalette(c))
        self.setStyleSheet(theme.stylesheet(c))
        self.setWindowIcon(theme.app_icon(c.accent))
        self.mark.setPixmap(theme.mark_pixmap(c.accent, 20))
        self.meter.set_colors(c.border_strong if c.dark else c.hover, c.ok, c.warn, c.live)
        self.more_button.setIcon(theme.glyph_icon(Glyph.MORE, c.text2))
        self.view.restyle(c)
        self._update_pin()
        self._update_controls()
        self.set_status(self.status.text(), self.tone)
        theme.tint_title_bar(self, c)

    def apply_font_size(self):
        self.view.setFont(theme.font(theme.READING_FONTS, self.font_size))

    def step_font(self, delta):
        sizes = FONT_SIZES
        nearest = min(range(len(sizes)), key=lambda i: abs(sizes[i] - self.font_size))
        self.font_size = sizes[max(0, min(len(sizes) - 1, nearest + delta))]
        self.settings.setValue('font/size', self.font_size)
        self.apply_font_size()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.settings.setValue('window/pinned', self.pinned)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pinned)
        self.show()  # Changing window flags recreates the native window.
        theme.tint_title_bar(self, self.colors)
        self._update_pin()

    def _update_pin(self):
        c = self.colors
        self.pin_button.setChecked(self.pinned)
        self.pin_button.setIcon(theme.glyph_icon(Glyph.PINNED if self.pinned else Glyph.PIN,
                                                 c.accent if self.pinned else c.text2))
        self.pin_button.setToolTip('取消置顶' if self.pinned else '窗口置顶，始终显示在其他窗口上方')

    def apply_hotkey(self, combo):
        ok = self.hotkey.register(combo) if self.hotkey else True
        self.settings.setValue('hotkey', combo)
        for action in self.hotkey_actions.actions():
            action.setChecked(action.data() == combo)
        if combo and ok:
            self.hotkey_note.setText(f'{combo} 复制最近一句')
            self.view.empty_hint = f'在任何窗口按 {combo} 复制最近一句 · 连按多带一段'
        else:
            self.hotkey_note.setText('')
            self.view.empty_hint = '选中文字，或点击左侧时间选择整段'
            if combo and not ok:
                self.set_status(f'快捷键 {combo} 被其他程序占用，请在「更多」菜单里换一个。', WARN)
        self.view.viewport().update()

    # ── status & controls ─────────────────────────────────────────────────────
    def set_status(self, text, tone=IDLE):
        self.tone = tone or IDLE
        self.status.setText(text)
        if self.status.property('tone') != self.tone:
            self.status.setProperty('tone', self.tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        running = self.worker is not None
        if not running:
            label, pill = {OK: ('自检通过', OK), ERROR: ('出错', ERROR), WARN: ('请注意', WARN)}.get(self.tone, ('未开始', IDLE))
        elif self.stopping:
            label, pill = '正在停止', BUSY
        elif self.mode == 'selftest':
            label, pill = '自检中', BUSY
        elif self.tone == LIVE:
            label, pill = '转录中', LIVE
        elif self.tone == WARN:
            label, pill = '重连中', WARN
        else:
            label, pill = '连接中', BUSY
        self.pill.set_state(pill, label, self.colors.tone(pill), pulsing=running and pill in (LIVE, BUSY, WARN))
        self.view.live = running and self.mode == 'live' and self.tone == LIVE

    def _update_controls(self):
        running = self.worker is not None
        has_source = self.source.currentData() is not None
        c = self.colors
        self.source.setEnabled(not running)
        self.selftest_action.setEnabled(not running and has_source)
        self.new_action.setEnabled(not running)
        if running:
            self.primary.setText(' 停止中…' if self.stopping else ' 停止')
            self.primary.setIcon(theme.shape_icon('stop', c.muted if self.stopping else c.live))
            self.primary.setEnabled(not self.stopping)
            kind = 'danger'
        else:
            self.primary.setText(' 开始')
            self.primary.setIcon(theme.shape_icon('play', c.on_accent if has_source else c.muted))
            self.primary.setEnabled(has_source)
            kind = 'primary'
        if self.primary.property('kind') != kind:
            self.primary.setProperty('kind', kind)
            self.primary.style().unpolish(self.primary)
            self.primary.style().polish(self.primary)

    def refresh_sources(self):
        if self.worker:
            return
        wanted = self.source.currentData().uid if self.source.currentData() else self.settings.value('source', '')
        self.source.blockSignals(True)
        self.source.clear()
        try:
            found = self.sources()
        except Exception:
            found = []
        icon = theme.glyph_icon(Glyph.SPEAKER, self.colors.text2, 14)
        for s in found:
            self.source.addItem(icon, s.name, s)
        index = next((i for i, s in enumerate(found) if s.uid == wanted), 0)
        if not found:
            self.source.addItem('没有找到扬声器')
            self.set_status('没有找到可录制的扬声器。请检查 Windows 声音设置。', ERROR)
        self.source.setCurrentIndex(index)
        self.source.blockSignals(False)
        # sizeHint ignores QSS padding, so size to the current label explicitly.
        label = self.source.fontMetrics().horizontalAdvance(self.source.currentText())
        self.source.setMinimumWidth(min(360, label + 64))
        self._update_controls()

    def _source_chosen(self):
        if self.source.currentData() is not None:
            self.settings.setValue('source', self.source.currentData().uid)
        self._update_controls()

    # ── transcript ────────────────────────────────────────────────────────────
    def add_segment(self, kind, text, note=''):
        seg = self.view.push_segment(kind, text)
        if self.recorder.write(seg, note or text):
            self.saved.setText(f'已自动保存 · {self.recorder.path.name}')
            self.saved.show()
        return seg

    def flash(self, text):
        self.toast.setText(text)
        self.toast_timer.start(2400)

    def copy_recent(self):
        """Global hotkey: copy what was just said; repeated presses reach further back."""
        last, extra = self.hotkey_presses
        now = time.monotonic()
        extra = extra + 1 if now - last < HOTKEY_REPEAT else 0
        text, rows = self.view.recent(extra)
        self.hotkey_presses = (now, extra)
        if not text:
            self.flash('还没有可以复制的内容')
            return
        QApplication.clipboard().setText(text)
        self.view.flash(rows)
        self.flash(f'已复制 {len(rows)} 段 · 再按一次多带一段')

    def copy_all(self):
        text = '\n\n'.join(s.text for s in self.view.segments if s.kind in (FINAL, PROVISIONAL))
        if text:
            QApplication.clipboard().setText(text)
            self.flash('已复制全部内容')

    def open_records(self):
        folder = self.recorder.folder
        folder.mkdir(parents=True, exist_ok=True)
        if hasattr(os, 'startfile'):
            os.startfile(folder)

    def new_transcript(self):
        if self.worker or not self.view.segments:
            return
        answer = QMessageBox.question(self, '新建记录', '清空屏幕并开始一份新记录？\n当前内容已经自动保存，不会丢失。',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.view.reset()
            self.recorder.new_file()
            self.saved.hide()

    # ── engine lifecycle ──────────────────────────────────────────────────────
    def toggle(self):
        if self.worker:
            self.stop()
        else:
            self.start()

    def start(self, mode='live'):
        if self.worker:
            return
        source = self.source.currentData()
        if source is None:
            return
        if mode == 'live' and self.view.segments:
            label = f'{datetime.now():%H:%M} 重新开始'
            self.add_segment(RESUME, label)
        self.mailbox = Mailbox()
        self.worker = self.engine_factory(source, self.mailbox, mode=mode)
        self.mode = mode
        self.stopping = False
        self.set_status('正在启动…', BUSY)
        self._update_controls()
        self.worker.start()

    def stop(self):
        if self.worker and not self.stopping:
            self.worker.stop()
            self.stopping = True
            self.set_status('正在停止…', BUSY)
            self._update_controls()

    def _absorb(self, snap):
        for kind, value in snap.events:
            if kind == COMMIT:
                self.add_segment(FINAL, value)
            elif kind == PROVISIONAL_EVENT:
                self.add_segment(PROVISIONAL, value)
            elif kind == GAP_EVENT:
                self.add_segment(GAP, f'连接中断约 {max(1, round(value))} 秒 · 已自动恢复')
        if snap.partial is not None:
            self.view.push_partial(snap.partial)
        if snap.status is not None:
            self.set_status(snap.status, snap.tone)

    def tick(self):
        if self.mailbox:
            snap = self.mailbox.drain()
            self._absorb(snap)
            self.meter.set_peak(snap.level)
        self.meter.advance()
        if self.worker and not self.worker.is_alive():
            self.worker.join()
            self._absorb(self.mailbox.drain())  # The final status must never be lost.
            if self.mode == 'live':
                text = self.view.partial_text
                if text.strip():
                    self.add_segment(PROVISIONAL, text)
                self.view.push_partial('')
            self.worker = self.mailbox = self.mode = None
            self.stopping = False
            self.meter.set_peak(0)
            self.set_status(self.status.text(), self.tone)
            self._update_controls()
        self.view.flush()
        self.view.tick()
        if self.pill.dot.pulsing:
            self.pill.dot.update()
        if self.closing and self.worker is None:
            self.close()

    # ── window ────────────────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        theme.tint_title_bar(self, self.colors)

    def closeEvent(self, event):
        if self.worker:
            self.closing = True
            self.stop()
            event.ignore()
            return
        if self.hotkey:
            self.hotkey.unregister()
        self.settings.setValue('window/geometry', self.saveGeometry())
        event.accept()
