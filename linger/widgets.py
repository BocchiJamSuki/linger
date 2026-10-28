"""Small painted widgets: level meter, pulsing dot, status pill."""
import math
import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class LevelMeter(QWidget):
    """Peak meter in dBFS with fast attack, smooth release and a short peak hold."""
    FLOOR = -60.0

    def __init__(self):
        super().__init__()
        self.target = self.shown = self.hold = self.FLOOR
        self.hold_until = 0.0
        self.colors = ('#333', '#3FD48B', '#F2B54A', '#FF6464')
        self.setFixedHeight(4)
        self.setFixedWidth(56)

    def set_colors(self, track, ok, warn, hot):
        self.colors = (track, ok, warn, hot)
        self.update()

    @staticmethod
    def to_db(peak):
        return 20 * math.log10(peak) if peak > 0 else -math.inf

    def set_peak(self, peak):
        self.target = max(self.FLOOR, self.to_db(peak))

    def advance(self):
        before = (self.shown, self.hold)
        self.shown = self.target if self.target > self.shown else max(self.target, self.shown - 1.2)
        now = time.monotonic()
        if self.shown >= self.hold:
            self.hold, self.hold_until = self.shown, now + 0.9
        elif now > self.hold_until:
            self.hold = max(self.shown, self.hold - 0.8)
        if before != (self.shown, self.hold):
            self.update()

    def reset(self):
        self.target = self.shown = self.hold = self.FLOOR
        self.update()

    def _fraction(self, db):
        return max(0.0, min(1.0, (db - self.FLOOR) / -self.FLOOR))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        track = QPainterPath()
        track.addRoundedRect(r, radius, radius)
        p.fillPath(track, QColor(self.colors[0]))
        fill = self._fraction(self.shown)
        if fill > 0:
            grad = QLinearGradient(r.left(), 0, r.right(), 0)
            grad.setColorAt(0.0, QColor(self.colors[1]))
            grad.setColorAt(0.72, QColor(self.colors[1]))
            grad.setColorAt(0.86, QColor(self.colors[2]))
            grad.setColorAt(1.0, QColor(self.colors[3]))
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(r.left(), r.top(), max(r.height(), r.width() * fill), r.height()), radius, radius)
            p.fillPath(bar.intersected(track), grad)
        hold = self._fraction(self.hold)
        if hold > 0.02:
            x = r.left() + r.width() * hold
            p.fillRect(QRectF(min(x, r.right() - 2), r.top(), 2, r.height()), QColor(self.colors[1 if hold < 0.86 else 3]))
        p.end()


class PulseDot(QWidget):
    def __init__(self, size=8):
        super().__init__()
        self.color = QColor('#888')
        self.pulsing = False
        self.dot = size
        self.setFixedSize(QSize(size + 8, size + 8))

    def set_state(self, color, pulsing):
        self.color, self.pulsing = QColor(color), pulsing
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.rect().center()
        center = QRectF(c.x() - self.dot / 2 + 0.5, c.y() - self.dot / 2 + 0.5, self.dot, self.dot).center()
        if self.pulsing:
            phase = (time.monotonic() % 1.6) / 1.6
            halo = QColor(self.color)
            halo.setAlphaF(0.45 * (1 - phase))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            radius = self.dot / 2 + phase * 4
            p.drawEllipse(center, radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(center, self.dot / 2, self.dot / 2)
        p.end()


class StatusPill(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName('pill')
        self.dot = PulseDot(7)
        self.label = QLabel()
        self.label.setObjectName('pillText')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 12, 3)
        layout.setSpacing(2)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.tone = None

    def set_state(self, tone, text, color, pulsing=False):
        self.label.setText(text)
        self.dot.set_state(color, pulsing)
        if tone != self.tone:
            self.tone = tone
            for w in (self, self.label):
                w.setProperty('tone', tone)
                repolish(w)
