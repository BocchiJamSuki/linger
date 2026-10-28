"""The transcript: one continuous, append-only stream with a clickable time gutter.

Committed speech is written once and never touched again, so text the reader is looking at or
selecting cannot move. Only the last block, the words still being recognised, changes; updates
to it wait while the reader is dragging or has selected into it.
"""
from collections import deque
import time

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont, QFontMetricsF, QKeySequence, QPainter, QTextBlockFormat, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QTextEdit, QWidget

from . import theme
from .transcript import GAP, PROVISIONAL, RESUME, SPEECH, Segment, join_speech, recent_group

PARTIAL = -100  # userState of the live block (Qt's default for new blocks is -1).


def clock(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


class Gutter(QWidget):
    """Elapsed time per segment; click / Shift-click / Ctrl-click / drag to pick whole segments."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setMouseTracking(True)

    def paintEvent(self, event):
        self.view.paint_gutter(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.view.pick_at(event.position().y(), event.modifiers(), extend=False)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.view.pick_at(event.position().y(), Qt.KeyboardModifier.ShiftModifier, extend=True)
        self.view.hover(event.position().y())

    def leaveEvent(self, event):
        self.view.hover(None)


class TranscriptView(QTextEdit):
    GUTTER = 64
    MAX_COLUMN = 840
    LINE_HEIGHT = 140
    GAP_AFTER = 8

    def __init__(self):
        super().__init__()
        self.setObjectName('transcript')
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.document().setDocumentMargin(6)
        self.colors = theme.DARK
        self.empty_hint = ''
        self.live = False
        self.segments = []
        self.block_of = []          # Segment index → block number (blocks are only appended).
        self.ops = deque()          # Segments waiting while the live block is protected.
        self.pending_partial = None
        self.partial_start = None
        self.origin = None
        self.dragging = self.menu_open = False
        self.follow = True
        self.unseen = 0
        self.picked = set()
        self.anchor = None
        self.hover_row = None
        self.flashing = ([], 0.0)
        self._programmatic = False

        self.gutter = Gutter(self)
        self.jump = QPushButton(self)
        self.jump.setProperty('kind', 'floating')
        self.jump.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump.clicked.connect(self.jump_to_latest)
        self.jump.hide()
        self.selbar = QFrame(self)
        self.selbar.setObjectName('selbar')
        bar = QHBoxLayout(self.selbar)
        bar.setContentsMargins(10, 3, 4, 3)
        bar.setSpacing(2)
        self.selbar_text = QLabel()
        self.selbar_text.setObjectName('selbarText')
        self.copy_button = QPushButton('复制')
        self.copy_button.setProperty('kind', 'bar')
        self.cancel_button = QPushButton('取消')
        self.cancel_button.setProperty('kind', 'barquiet')
        for w in (self.selbar_text, self.copy_button, self.cancel_button):
            bar.addWidget(w)
        self.copy_button.clicked.connect(self.copy_selection)
        self.cancel_button.clicked.connect(self.clear_selection)
        self.selbar.hide()
        self.on_copy = lambda n: None   # Window hook for feedback.

        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.reset()

    # ── formats ───────────────────────────────────────────────────────────────
    def _speech_block(self):
        fmt = QTextBlockFormat()
        fmt.setLineHeight(self.LINE_HEIGHT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        fmt.setBottomMargin(self.GAP_AFTER)
        return fmt

    def _marker_block(self):
        fmt = QTextBlockFormat()
        fmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fmt.setTopMargin(4)
        fmt.setBottomMargin(self.GAP_AFTER + 4)
        return fmt

    def _char(self, kind):
        fmt = QTextCharFormat()
        c = self.colors
        if kind == PARTIAL:
            fmt.setForeground(theme.qcolor(c.text2))
        elif kind == PROVISIONAL:
            fmt.setForeground(theme.qcolor(c.text2))
            fmt.setFontItalic(True)
        elif kind in (GAP, RESUME):
            fmt.setForeground(theme.qcolor(c.muted))
            fmt.setFont(theme.font(theme.UI_FONTS, 12))
        return fmt

    # ── feeding ───────────────────────────────────────────────────────────────
    def push_partial(self, text):
        if text.strip() and self.partial_start is None:
            self.partial_start = time.time()
        self.pending_partial = text

    def push_segment(self, kind, text):
        now = time.time()
        start = now
        if kind in SPEECH:
            start = self.partial_start or now
            self.partial_start = None
        seg = Segment(kind, text, start, now)
        if self.origin is None and kind in SPEECH:
            self.origin = start
        self.ops.append(seg)
        return seg

    @property
    def partial_text(self):
        if self.pending_partial is not None:
            return self.pending_partial
        return self.document().lastBlock().text()

    def protected(self):
        """True while the live block must not change under the reader."""
        if self.dragging or self.menu_open:
            return True
        cursor = self.textCursor()
        return cursor.hasSelection() and cursor.selectionEnd() > self.document().lastBlock().position()

    def reading(self):
        return self.dragging or bool(self.picked) or self.textCursor().hasSelection()

    def flush(self):
        """Apply queued changes (called every frame). Returns the number of segments added."""
        added = 0
        if not self.protected() and (self.ops or self.pending_partial is not None):
            bar = self.verticalScrollBar()
            old = bar.value()
            cursor = QTextCursor(self.document())
            cursor.beginEditBlock()
            while self.ops:
                self._append(cursor, self.ops.popleft())
                added += 1
            if self.pending_partial is not None:
                self._set_partial(cursor, self.pending_partial)
                self.pending_partial = None
            cursor.endEditBlock()
            if not (self.follow and not self.reading()):
                self._set_scroll(old)
                self.unseen += added
        if self.follow and not self.reading():
            self._to_bottom()
        self._update_floating()
        return added

    def _append(self, cursor, seg):
        index = len(self.segments)
        self.segments.append(seg)
        live = self.document().lastBlock()
        carry = '' if seg.kind in SPEECH else live.text()  # Markers must not swallow live words.
        cursor.setPosition(live.position())
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.setBlockFormat(self._speech_block() if seg.kind in SPEECH else self._marker_block())
        cursor.insertText(seg.text, self._char(seg.kind))
        live.setUserState(index)
        self.block_of.append(live.blockNumber())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock(self._speech_block(), self._char(PARTIAL))
        if carry:
            cursor.insertText(carry, self._char(PARTIAL))
        self.document().lastBlock().setUserState(PARTIAL)

    def _set_partial(self, cursor, text):
        live = self.document().lastBlock()
        if live.text() == text:
            return
        cursor.setPosition(live.position())
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        if text:
            cursor.insertText(text, self._char(PARTIAL))
        else:
            cursor.removeSelectedText()
        if not text:
            self.partial_start = None

    def finalize_partial(self):
        """On stop: keep unconfirmed trailing words as a provisional segment."""
        text = self.partial_text
        if text.strip():
            self.push_segment(PROVISIONAL, text)
        self.push_partial('')

    def reset(self):
        self.clear()
        self.segments.clear()
        self.block_of.clear()
        self.ops.clear()
        self.pending_partial = None
        self.partial_start = self.origin = None
        self.picked.clear()
        self.anchor = self.hover_row = None
        self.unseen = 0
        self.follow = True
        cursor = QTextCursor(self.document())
        cursor.setBlockFormat(self._speech_block())
        self.document().lastBlock().setUserState(PARTIAL)
        self._update_floating()
        self.viewport().update()
        self.gutter.update()

    def restyle(self, colors):
        self.colors = colors
        cursor = QTextCursor(self.document())
        block = self.document().begin()
        while block.isValid():
            state = block.userState()
            kind = PARTIAL if state == PARTIAL else (self.segments[state].kind if 0 <= state < len(self.segments) else None)
            if kind is not None and block.length() > 1:
                cursor.setPosition(block.position())
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(self._char(kind))
            block = block.next()
        self.viewport().update()
        self.gutter.update()

    # ── scrolling ─────────────────────────────────────────────────────────────
    def _set_scroll(self, value):
        self._programmatic = True
        self.verticalScrollBar().setValue(value)
        self._programmatic = False

    def _to_bottom(self):
        bar = self.verticalScrollBar()
        if bar.value() != bar.maximum():
            self._set_scroll(bar.maximum())
        self.unseen = 0

    def _scrolled(self, value):
        if not self._programmatic:
            bar = self.verticalScrollBar()
            self.follow = value >= bar.maximum() - 4
            if self.follow:
                self.unseen = 0
            self._update_floating()
        self.gutter.update()

    def jump_to_latest(self):
        self.clear_selection()
        self.follow = True
        self._to_bottom()
        self._update_floating()

    # ── selection ─────────────────────────────────────────────────────────────
    def row_at(self, y):
        block = self.cursorForPosition(QPoint(2, int(y))).block()
        state = block.userState()
        if 0 <= state < len(self.segments) and self.segments[state].kind in SPEECH:
            rect = self._block_rect(block)
            if rect.top() - 8 <= y <= rect.bottom() + self.GAP_AFTER + 8:
                return state
        return None

    def pick_at(self, y, modifiers, extend):
        row = self.row_at(y)
        if row is None:
            return
        if not extend:
            cursor = self.textCursor()
            cursor.clearSelection()
            self.setTextCursor(cursor)
            self.setFocus()
        speech = lambda a, b: {i for i in range(min(a, b), max(a, b) + 1) if self.segments[i].kind in SPEECH}
        if extend or (modifiers & Qt.KeyboardModifier.ShiftModifier and self.anchor is not None):
            self.picked = speech(self.anchor if self.anchor is not None else row, row)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            self.picked ^= {row}
            self.anchor = row
        elif self.picked == {row}:
            self.picked, self.anchor = set(), None
        else:
            self.picked, self.anchor = {row}, row
        self._update_floating()
        self.viewport().update()
        self.gutter.update()

    def hover(self, y):
        row = None if y is None else self.row_at(y)
        self.gutter.setCursor(Qt.CursorShape.PointingHandCursor if row is not None else Qt.CursorShape.ArrowCursor)
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
            self.gutter.update()

    def clear_selection(self):
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.picked.clear()
        self.anchor = None
        self._update_floating()
        self.viewport().update()
        self.gutter.update()

    def selected_text(self):
        if self.picked:
            return join_speech(self.segments[i].text for i in sorted(self.picked))
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return ''
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        parts = []
        block = self.document().findBlock(start)
        while block.isValid() and block.position() <= end:
            state = block.userState()
            if state == PARTIAL or (0 <= state < len(self.segments) and self.segments[state].kind in SPEECH):
                base = block.position()
                parts.append(block.text()[max(start, base) - base:min(end, base + block.length() - 1) - base])
            block = block.next()
        return join_speech(parts)

    def copy_selection(self):
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)
            self.on_copy(len(self.picked) or 1)
        return text

    def createMimeDataFromSelection(self):
        mime = QMimeData()
        mime.setText(self.selected_text())
        return mime

    def recent(self, extra=0):
        """(text, rows) for the latest utterance, for the global hotkey."""
        partial = (self.partial_text, self.partial_start or time.time()) if self.partial_text.strip() else None
        rows = recent_group(self.segments, partial, extra)
        texts = [self.partial_text if i < 0 else self.segments[i].text for i in rows]
        return join_speech(texts), rows

    def flash(self, rows):
        self.flashing = (list(rows), time.monotonic() + 1.4)
        self.viewport().update()

    # ── events ────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        self.dragging = True
        if self.picked:
            self.picked.clear()
            self.anchor = None
            self._update_floating()
            self.gutter.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.dragging = False

    def focusOutEvent(self, event):
        self.dragging = False
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            event.accept()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self.menu_open = True
        try:
            menu = QMenu(self)
            copy = menu.addAction('复制')
            copy.setShortcut(QKeySequence.StandardKey.Copy)
            copy.setEnabled(bool(self.selected_text()))
            copy.triggered.connect(self.copy_selection)
            menu.addAction('全选').triggered.connect(self.selectAll)
            menu.exec(event.globalPos())
        finally:
            self.menu_open = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        column = min(self.MAX_COLUMN, max(200, width - self.GUTTER - 40))
        side = max(8, (width - self.GUTTER - column) // 2)
        self.setViewportMargins(side + self.GUTTER, 0, side, 0)
        rect = self.contentsRect()
        self.gutter.setGeometry(rect.left() + side, rect.top(), self.GUTTER, rect.height())
        self._update_floating()

    def _update_floating(self):
        rect = self.contentsRect()
        if self.picked:
            n = len(self.picked)
            self.selbar_text.setText(f'已选 {n} 段')
            self.selbar.adjustSize()
            self.selbar.move(rect.center().x() - self.selbar.width() // 2, rect.bottom() - self.selbar.height() - 16)
            self.selbar.show()
            self.selbar.raise_()
            self.jump.hide()
            return
        self.selbar.hide()
        show_jump = not self.follow or (self.reading() and self.unseen)
        if show_jump:
            self.jump.setText(f'{self.unseen} 段新内容  ↓' if self.unseen else '回到最新  ↓')
            self.jump.adjustSize()
            self.jump.move(rect.center().x() - self.jump.width() // 2, rect.bottom() - self.jump.height() - 16)
            self.jump.raise_()
        self.jump.setVisible(bool(show_jump))

    # ── painting ──────────────────────────────────────────────────────────────
    def _block_rect(self, block):
        rect = self.document().documentLayout().blockBoundingRect(block)
        return rect.translated(0, -self.verticalScrollBar().value())

    def _row_rect(self, block):
        rect = self._block_rect(block)
        # The rect covers the glyph lines only; line-height padding and margins come after it.
        return QRectF(rect.left() - 10, rect.top() - 5, rect.width() + 20, rect.height() + 10)

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        c = self.colors
        doc = self.document()
        rows, until = self.flashing
        flash_alpha = max(0.0, min(1.0, (until - time.monotonic()) / 0.8)) if rows else 0
        marks = []
        if self.hover_row is not None and self.hover_row not in self.picked:
            marks.append((self.hover_row, theme.qcolor(c.hover)))
        marks += [(i, theme.qcolor(c.accent, 0.16 if c.dark else 0.12)) for i in self.picked]
        if flash_alpha:
            marks += [(i, theme.qcolor(c.ok, 0.22 * flash_alpha)) for i in rows]
        for row, color in marks:
            block = doc.lastBlock() if row < 0 else doc.findBlockByNumber(self.block_of[row])
            p.setBrush(color)
            p.drawRoundedRect(self._row_rect(block), 8, 8)
        if not self.segments and not self.partial_text.strip():
            self._paint_empty(p)
        p.end()
        super().paintEvent(event)

    def _paint_empty(self, p):
        c = self.colors
        rect = QRectF(self.viewport().rect())
        p.setPen(theme.qcolor(c.text2))
        p.setFont(theme.font(theme.UI_FONTS, 16, QFont.Weight.DemiBold))
        p.drawText(rect.adjusted(0, -24, 0, -24), Qt.AlignmentFlag.AlignCenter, '对方说的话会实时出现在这里')
        p.setPen(theme.qcolor(c.muted))
        p.setFont(theme.font(theme.UI_FONTS, 13))
        p.drawText(rect.adjusted(0, 22, 0, 22), Qt.AlignmentFlag.AlignCenter, self.empty_hint)

    def paint_gutter(self, widget):
        p = QPainter(widget)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.colors
        f = theme.font(theme.MONO_FONTS, 11)
        p.setFont(f)
        metrics = QFontMetricsF(f)
        right = widget.width() - 14
        block = self.cursorForPosition(QPoint(2, 0)).block()
        height = widget.height()
        while block.isValid():
            rect = self._block_rect(block)
            if rect.top() > height:
                break
            line = block.layout().lineAt(0) if block.layout().lineCount() else None
            if line is not None:
                baseline = rect.top() + line.y() + line.ascent()
                state = block.userState()
                if state == PARTIAL and block.text().strip():
                    color = theme.qcolor(c.live if self.live else c.muted)
                    center = QPointF(right - 4, rect.top() + line.y() + line.height() / 2)
                    if self.live:
                        phase = (time.monotonic() % 1.6) / 1.6
                        halo = theme.qcolor(c.live, 0.4 * (1 - phase))
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(halo)
                        p.drawEllipse(center, 3.5 + phase * 4, 3.5 + phase * 4)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color)
                    p.drawEllipse(center, 3.5, 3.5)
                elif 0 <= state < len(self.segments) and self.segments[state].kind in SPEECH:
                    label = clock(self.segments[state].start - (self.origin or self.segments[state].start))
                    active = state in self.picked or state == self.hover_row
                    p.setPen(theme.qcolor(c.accent if active else c.muted))
                    p.drawText(QPointF(right - metrics.horizontalAdvance(label), baseline), label)
            block = block.next()
        p.end()

    def tick(self):
        """Per-frame animation upkeep: the live dot and fading copy highlights."""
        if self.live and self.partial_text.strip():
            self.gutter.update()
        rows, until = self.flashing
        if rows:
            if time.monotonic() > until:
                self.flashing = ([], 0.0)
            self.viewport().update()
