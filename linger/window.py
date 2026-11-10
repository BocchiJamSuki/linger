from datetime import datetime
import os
from pathlib import Path
import time

from PySide6.QtCore import QPoint, QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from . import hotkey, i18n, theme
from .audio import list_sources
from .engine import BUSY, COMMIT, ERROR, GAP as GAP_EVENT, IDLE, LIVE, OK, PROVISIONAL as PROVISIONAL_EVENT
from .engine import QUIET, WARN, Engine, Mailbox
from .i18n import tr
from .keydialog import KeyDialog
from .scribe import find_key
from .theme import Glyph
from .transcript import FINAL, GAP, PROVISIONAL, RESUME, Recorder
from .view import TranscriptView
from .widgets import LevelMeter, StatusPill

FONT_SIZES = (15, 16, 17, 18, 19, 20, 22, 24, 26, 28, 32)
DEFAULT_FONT_SIZE = 19
HOTKEY_REPEAT = 2.0  # Seconds within which another press reaches one segment further back.
AUTO = 'auto'        # Stored value for "let the service detect the spoken language".


def records_folder():
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return Path(base) / 'Linger'


class SourceBox(QComboBox):
    """Device chooser. Opens as a menu of our own: the stock combo popup cannot have clean
    rounded corners. Devices are re-scanned on every open, so a newly plugged one just appears.
    """

    def __init__(self, refresh):
        super().__init__()
        self.refresh = refresh
        self.menu = None

    def showPopup(self):
        self.refresh()
        menu = self.menu = theme.round_menu(QMenu(self))
        menu.setObjectName('sources')
        for index in range(self.count()):
            action = menu.addAction(self.itemIcon(index), self.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == self.currentIndex())
            action.setEnabled(self.itemData(index) is not None)
            action.triggered.connect(lambda _=False, i=index: self.setCurrentIndex(i))
        menu.setMinimumWidth(self.width())
        menu.aboutToHide.connect(self.hidePopup)
        menu.popup(self.mapToGlobal(self.rect().bottomLeft()) + QPoint(0, 4))

    def hidePopup(self):
        if self.menu is not None and self.menu.isVisible():
            self.menu.hide()
        super().hidePopup()


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
        self.menu = None
        self.hotkey_presses = (0.0, 0)
        self.hotkey_ok = True
        self.font_size = int(self.settings.value('font/size', DEFAULT_FONT_SIZE))
        self.pinned = self.settings.value('window/pinned', False, type=bool)
        self._use_language()

        self.setWindowTitle('Linger')
        self.setMinimumSize(560, 420)
        self.resize(940, 720)
        self._build()
        if self.pinned:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        geometry = self.settings.value('window/geometry')
        if geometry is not None:
            self.restoreGeometry(geometry)

        self.hotkey = hotkey.GlobalHotkey(QApplication.instance(), self.copy_recent) if hotkeys else None
        self.hotkey_ok = self.hotkey.register(self.hotkey_combo()) if self.hotkey else True
        self.apply_theme()
        self.apply_font_size()
        self.set_status(tr('status.ready'))
        self.retranslate()  # Builds the menu and fills every label; may report a device problem.
        if self.hotkey_combo() and not self.hotkey_ok:
            self.set_status(tr('status.hotkey_taken', combo=self.hotkey_combo()), WARN)

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
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.source.setMaximumWidth(360)
        self.source.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.source.currentIndexChanged.connect(self._source_chosen)
        bar.addWidget(self.source)
        bar.addStretch(1)
        self.pill = StatusPill()
        bar.addWidget(self.pill)
        bar.addSpacing(4)
        self.pin_button = self._icon_button()
        self.pin_button.setCheckable(True)
        self.pin_button.clicked.connect(self.toggle_pin)
        self.more_button = self._icon_button()
        self.more_button.clicked.connect(self.show_menu)
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
        self.view.on_copy = lambda n: self.flash(tr('toast.copied', n=n))
        page.addWidget(self.view, 1)

        status = QFrame()
        status.setObjectName('statusbar')
        row = QHBoxLayout(status)
        row.setContentsMargins(16, 6, 16, 7)
        row.setSpacing(10)
        self.meter = LevelMeter()
        row.addWidget(self.meter)
        self.status = QLabel()
        self.status.setObjectName('statusText')
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # Long texts elide the row, not widen the window.
        row.addWidget(self.status, 1)
        self.toast = QLabel()
        self.toast.setObjectName('toast')
        row.addWidget(self.toast)
        self.saved = QPushButton()
        self.saved.setObjectName('note')
        self.saved.setFlat(True)
        self.saved.setCursor(Qt.CursorShape.PointingHandCursor)
        self.saved.setStyleSheet('QPushButton { background: transparent; border: none; padding: 0; min-height: 0; }')
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

    def _icon_button(self):
        b = QPushButton()
        b.setProperty('kind', 'icon')
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _choices(self, menu, title, options, current, chosen):
        """A submenu of mutually exclusive options: [(value, label)]."""
        sub = theme.round_menu(menu.addMenu(title))
        group = QActionGroup(sub)
        for value, label in options:
            action = QAction(label, sub, checkable=True)
            action.setChecked(value == current)
            action.triggered.connect(lambda _=False, v=value: chosen(v))
            group.addAction(action)
            sub.addAction(action)
        return sub

    def _build_menu(self):
        menu = theme.round_menu(QMenu(self))
        menu.addAction(tr('menu.font_up'), lambda: self.step_font(1)).setShortcut(QKeySequence('Ctrl+='))
        menu.addAction(tr('menu.font_down'), lambda: self.step_font(-1)).setShortcut(QKeySequence('Ctrl+-'))
        menu.addSeparator()
        self._choices(menu, tr('menu.speech'),
                      [(code or AUTO, name or tr('menu.auto')) for code, name in i18n.SPEECH_LANGUAGES],
                      self.settings.value('speech/language', 'en'), self.set_speech_language)
        self._choices(menu, tr('menu.hotkey'), [(c, c) for c in hotkey.COMBOS] + [(None, tr('menu.off'))],
                      self.hotkey_combo(), self.apply_hotkey)
        menu.addSeparator()
        self._choices(menu, tr('menu.theme'),
                      [('system', tr('menu.system')), ('light', tr('menu.light')), ('dark', tr('menu.dark'))],
                      self.settings.value('theme', 'system'), self.set_theme)
        self._choices(menu, tr('menu.ui_language'),
                      [('system', tr('menu.system'))] + list(i18n.LANGUAGES.items()),
                      self.settings.value('ui/language', 'system'), self.set_ui_language)
        menu.addSeparator()
        self.key_action = menu.addAction(tr('menu.key'), self.edit_key)
        self.selftest_action = menu.addAction(tr('menu.selftest'), lambda: self.start('selftest'))
        menu.addSeparator()
        menu.addAction(tr('menu.copy_all'), self.copy_all)
        menu.addAction(tr('menu.folder'), self.open_records)
        self.new_action = menu.addAction(tr('menu.new'), self.new_transcript)
        if self.menu is not None:
            self.menu.deleteLater()
        self.menu = menu

    def show_menu(self):
        # Right-aligned under the button, so the menu never hangs off the window's edge.
        corner = self.more_button.mapToGlobal(self.more_button.rect().bottomRight())
        self.menu.popup(corner + QPoint(-self.menu.sizeHint().width(), 6))

    # ── language ──────────────────────────────────────────────────────────────
    def _use_language(self):
        choice = self.settings.value('ui/language', 'system')
        i18n.set_language(choice if choice in i18n.LANGUAGES else i18n.system_language())

    def set_ui_language(self, choice):
        self.settings.setValue('ui/language', choice)
        self._use_language()
        self.retranslate()
        if self.worker is None:
            self.set_status(tr('status.ready'))

    def set_speech_language(self, code):
        self.settings.setValue('speech/language', code)

    def speech_language(self):
        code = self.settings.value('speech/language', 'en')
        return None if code == AUTO else code

    def retranslate(self):
        """(Re)apply every static text in the current interface language."""
        self._build_menu()
        self.source.setToolTip(tr('source.tip'))
        self.more_button.setToolTip(tr('more'))
        self.meter.setToolTip(tr('meter.tip'))
        self.saved.setToolTip(tr('saved.tip'))
        if self.recorder.path is not None:
            self.saved.setText(tr('saved', name=self.recorder.path.name))
        combo = self.hotkey_combo()
        if combo and self.hotkey_ok:
            self.hotkey_note.setText(tr('hotkey.note', combo=combo))
            self.view.empty_hint = tr('empty.hotkey', combo=combo)
        else:
            self.hotkey_note.setText('')
            self.view.empty_hint = tr('empty.plain')
        self.view.retranslate()
        self._update_pin()
        self.refresh_sources()
        self.set_status(self.status.text(), self.tone)

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
        self.pin_button.setToolTip(tr('pin.off') if self.pinned else tr('pin.on'))

    def hotkey_combo(self):
        combo = self.settings.value('hotkey', hotkey.DEFAULT)
        return combo if combo in hotkey.COMBOS else None

    def apply_hotkey(self, combo):
        self.settings.setValue('hotkey', combo or '')
        self.hotkey_ok = self.hotkey.register(combo) if self.hotkey else True
        self.retranslate()
        if combo and not self.hotkey_ok:
            self.set_status(tr('status.hotkey_taken', combo=combo), WARN)

    # ── status & controls ─────────────────────────────────────────────────────
    def set_status(self, text, tone=IDLE):
        self.tone = tone or IDLE
        self.status.setText(text)
        self.status.setToolTip(text)
        if self.status.property('tone') != self.tone:
            self.status.setProperty('tone', self.tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        running = self.worker is not None
        if not running:
            label, pill = {OK: ('pill.ok', OK), ERROR: ('pill.error', ERROR),
                           WARN: ('pill.warn', WARN)}.get(self.tone, ('pill.idle', IDLE))
        elif self.stopping:
            label, pill = 'pill.stopping', BUSY
        elif self.mode == 'selftest':
            label, pill = 'pill.selftest', BUSY
        elif self.tone == LIVE:
            label, pill = 'pill.live', LIVE
        elif self.tone == QUIET:
            label, pill = 'pill.quiet', QUIET
        elif self.tone == WARN:
            label, pill = 'pill.reconnecting', WARN
        else:
            label, pill = 'pill.connecting', BUSY
        self.pill.set_state(pill, tr(label), self.colors.tone(pill), pulsing=running and pill in (LIVE, BUSY, WARN))
        self.view.live = running and self.mode == 'live' and self.tone in (LIVE, QUIET)

    def _update_controls(self):
        running = self.worker is not None
        has_source = self.source.currentData() is not None
        c = self.colors
        self.source.setEnabled(not running)
        if self.menu is not None:
            self.selftest_action.setEnabled(not running and has_source)
            self.new_action.setEnabled(not running)
            self.key_action.setEnabled(not running)
        if running:
            self.primary.setText(tr('stopping') if self.stopping else tr('stop'))
            self.primary.setIcon(theme.shape_icon('stop', c.muted if self.stopping else c.live))
            self.primary.setEnabled(not self.stopping)
            kind = 'danger'
        else:
            self.primary.setText(tr('start'))
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
            self.source.addItem(tr('source.none'))
            self.set_status(tr('status.no_source'), ERROR)
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
            self.saved.setText(tr('saved', name=self.recorder.path.name))
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
            self.flash(tr('toast.nothing'))
            return
        QApplication.clipboard().setText(text)
        self.view.flash(rows)
        self.flash(tr('toast.recent', n=len(rows)))

    def copy_all(self):
        text = '\n\n'.join(s.text for s in self.view.segments if s.kind in (FINAL, PROVISIONAL))
        if text:
            QApplication.clipboard().setText(text)
            self.flash(tr('toast.all'))

    def open_records(self):
        folder = self.recorder.folder
        folder.mkdir(parents=True, exist_ok=True)
        if hasattr(os, 'startfile'):
            os.startfile(folder)

    def new_transcript(self):
        if self.worker or not self.view.segments:
            return
        answer = QMessageBox.question(self, tr('new.title'), tr('new.body'),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.view.reset()
            self.recorder.new_file()
            self.saved.hide()

    def edit_key(self):
        """Returns True when a key was saved or removed."""
        dialog = KeyDialog(self)
        if not dialog.exec():
            return False
        self.set_status(dialog.outcome, OK)
        return True

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
        if not find_key()[0] and not (self.edit_key() and find_key()[0]):
            return  # Ask for the key up front instead of failing after the click.
        if mode == 'live' and self.view.segments:
            self.add_segment(RESUME, tr('mark.resume', time=f'{datetime.now():%H:%M}'))
        self.mailbox = Mailbox()
        self.worker = self.engine_factory(source, self.mailbox, mode=mode, language=self.speech_language())
        self.mode = mode
        self.stopping = False
        self.set_status(tr('status.starting'), BUSY)
        self._update_controls()
        self.worker.start()

    def stop(self):
        if self.worker and not self.stopping:
            self.worker.stop()
            self.stopping = True
            self.set_status(tr('status.stopping'), BUSY)
            self._update_controls()

    def _absorb(self, snap):
        for kind, value in snap.events:
            if kind == COMMIT:
                self.add_segment(FINAL, value)
            elif kind == PROVISIONAL_EVENT:
                self.add_segment(PROVISIONAL, value)
            elif kind == GAP_EVENT:
                self.add_segment(GAP, tr('mark.gap', n=max(1, round(value))))
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
    def bring_to_front(self):
        """Another launch was attempted: show this window instead of a second copy."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

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
