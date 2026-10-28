"""Transcript view and window behaviour with real Qt widgets and input events."""
import time

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from linger.audio import Source
from linger.transcript import FINAL, GAP
from linger.view import PARTIAL, TranscriptView
from linger.window import Window


@pytest.fixture
def clipboard():
    board = QApplication.clipboard()
    old = board.text()
    yield board
    board.setText(old)


@pytest.fixture
def view(qtbot):
    v = TranscriptView()
    qtbot.addWidget(v)
    v.resize(700, 400)
    v.show()
    return v


def feed(v, *texts):
    for t in texts:
        v.push_segment(FINAL, t)
    v.flush()


def select(v, start, end):
    cursor = v.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    v.setTextCursor(cursor)
    return cursor.selectedText()


def gutter_y(v, row):
    block = v.document().findBlockByNumber(v.block_of[row])
    return v._block_rect(block).center().y()


def test_committed_text_never_moves_while_selected(view, clipboard):
    feed(view, 'The professor explains the experimental results.', 'Please compare both methods.')
    view.setFocus()
    selected = select(view, 4, 65)  # Across two segments.
    before = view.verticalScrollBar().value()
    started = time.perf_counter()
    for i in range(3000):
        view.push_partial(f'Continuing the discussion, revision {i}')
        if i % 100 == 0:
            view.push_segment(FINAL, f'New committed sentence {i}.')
        view.flush()
    elapsed = time.perf_counter() - started
    # Appends land below the selection without touching it or the scroll position.
    assert view.textCursor().selectedText() == selected
    assert view.verticalScrollBar().value() == before
    assert 'New committed sentence 2900.' in view.toPlainText()
    assert view.partial_text == 'Continuing the discussion, revision 2999'
    assert view.jump.isVisibleTo(view)
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    # Segments are pause-delimited fragments, so they join with spaces for translators.
    assert clipboard.text() == 'professor explains the experimental results. Please compare b'
    assert elapsed < 15


def test_live_words_freeze_only_while_selected(view):
    feed(view, 'Done.')
    view.push_partial('An unfamiliar technical word')
    view.flush()
    base = view.document().lastBlock().position()
    select(view, base + 3, base + 13)
    view.push_partial('An unfamiliar technical wording was revised')
    view.push_segment(FINAL, 'An unfamiliar technical wording was revised.')
    view.flush()
    assert view.partial_text != '' and view.document().lastBlock().text() == 'An unfamiliar technical word'
    assert view.textCursor().selectedText() == 'unfamiliar'
    QTest.keyClick(view, Qt.Key.Key_Escape)
    view.flush()
    assert view.segments[-1].text == 'An unfamiliar technical wording was revised.'
    assert view.document().lastBlock().userState() == PARTIAL


def test_mouse_drag_holds_updates_until_release(view):
    feed(view, 'The professor explains the experimental results.')
    view.push_partial('live words')
    view.flush()
    cursor = view.textCursor()
    cursor.setPosition(4)
    start = view.cursorRect(cursor).center()
    cursor.setPosition(23)
    end = view.cursorRect(cursor).center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    view.push_partial('changed while dragging')
    view.flush()
    assert view.document().lastBlock().text() == 'live words'
    QTest.mouseMove(view.viewport(), end, delay=10)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert view.textCursor().selectedText() == 'professor explains '
    view.flush()
    assert view.document().lastBlock().text() == 'changed while dragging'


def test_gutter_picks_whole_segments(view, clipboard):
    feed(view, 'Now, in your proposal,', 'I mean the second one,', 'how would you test it?', 'Unrelated.')
    y = [gutter_y(view, i) for i in range(4)]
    QTest.mouseClick(view.gutter, Qt.MouseButton.LeftButton, pos=QPoint(20, int(y[0])))
    QTest.mouseClick(view.gutter, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, QPoint(20, int(y[2])))
    assert view.picked == {0, 1, 2} and view.selbar.isVisibleTo(view)
    QTest.mouseClick(view.gutter, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(20, int(y[1])))
    assert view.picked == {0, 2}
    view.copy_button.click()
    assert clipboard.text() == 'Now, in your proposal, how would you test it?'
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert not view.picked and not view.selbar.isVisibleTo(view)


def test_follow_pauses_when_scrolled_up_and_resumes(view):
    feed(view, *[f'Sentence number {i}.' for i in range(80)])
    bar = view.verticalScrollBar()
    assert bar.value() == bar.maximum() > 0
    bar.setValue(10)
    assert not view.follow
    feed(view, 'Newest.')
    assert bar.value() == 10 and view.unseen == 1
    assert '1 段新内容' in view.jump.text()
    view.jump.click()
    assert view.follow and bar.value() == bar.maximum() and not view.jump.isVisibleTo(view)


def test_markers_are_not_copied_and_keep_live_words(view, clipboard):
    feed(view, 'Before.')
    view.push_partial('half')
    view.push_segment(GAP, '连接中断约 2 秒')
    view.flush()
    assert view.partial_text == 'half'
    view.selectAll()
    assert view.selected_text() == 'Before. half'


@pytest.fixture
def make_window(qtbot, tmp_path, talker):
    def make(**kwargs):
        settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
        kwargs.setdefault('engine_factory', talker)
        kwargs.setdefault('sources', lambda: [Source('', 'Default speaker'), Source('b', 'Other speaker')])
        w = Window(settings=settings, hotkeys=False, records=tmp_path / 'records', **kwargs)
        qtbot.addWidget(w)
        return w
    return make


def test_start_stop_restart_autosaves_and_closes(make_window, qtbot, tmp_path):
    w = make_window()
    w.show()
    for run in range(2):
        QTest.mouseClick(w.primary, Qt.MouseButton.LeftButton)
        assert w.worker is not None and w.primary.property('kind') == 'danger' and not w.source.isEnabled()
        qtbot.waitUntil(lambda: len(w.view.segments) >= 2 + run * 3, timeout=3000)
        assert w.pill.label.text() == '转录中' and w.view.live
        QTest.mouseClick(w.primary, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: w.worker is None, timeout=3000)
        assert w.primary.property('kind') == 'primary' and w.pill.label.text() == '未开始'
    kinds = [s.kind for s in w.view.segments]
    assert 'resume' in kinds  # Second run is marked as a restart.
    saved = list((tmp_path / 'records').glob('*.txt'))
    assert len(saved) == 1 and 'Sentence 10.' in saved[0].read_text(encoding='utf-8')
    w.start()
    worker = w.worker
    w.close()
    qtbot.waitUntil(lambda: not w.isVisible(), timeout=3000)
    assert not worker.is_alive()


def test_hotkey_copies_latest_utterance_and_reaches_back(make_window, clipboard):
    w = make_window()
    base = time.time()
    for offset, text in ((0, 'Old topic.'), (20, 'About your proposal,'), (21, 'I mean the second one,'),
                         (22, 'how would you test it?')):
        seg = w.view.push_segment(FINAL, text)
        seg.start, seg.end = base + offset, base + offset + 0.8
    w.view.flush()
    w.copy_recent()
    assert clipboard.text() == 'About your proposal, I mean the second one, how would you test it?'
    w.copy_recent()  # Pressed again quickly: one segment further back.
    assert clipboard.text().startswith('Old topic. About your proposal,')
    assert '已复制 4 段' in w.toast.text()


def test_selftest_and_preferences(make_window):
    w = make_window()
    w.start('selftest')
    w.worker.join()
    w.tick()
    assert w.pill.label.text() == '自检通过'
    size = w.font_size
    w.step_font(1)
    assert w.font_size > size and int(w.settings.value('font/size')) == w.font_size
    w.source.setCurrentIndex(1)
    assert w.settings.value('source') == 'b'
    dark = w.colors.dark
    w.set_theme('light' if dark else 'dark')
    assert w.colors.dark is not dark


def test_remembers_source_and_handles_no_devices(make_window):
    w = make_window()
    w.source.setCurrentIndex(1)
    again = make_window()
    assert again.source.currentData().uid == 'b'
    empty = make_window(sources=lambda: [])
    assert not empty.primary.isEnabled()
    empty.start()
    assert empty.worker is None and empty.pill.label.text() == '出错'
