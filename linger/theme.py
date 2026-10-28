"""Visual language: two palettes, one stylesheet, drawn icons, Windows title bar tint."""
from dataclasses import dataclass, fields
from pathlib import Path
from string import Template
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPixmap

ASSETS = Path(__file__).resolve().parent / 'assets'
UI_FONTS = ['Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI']
READING_FONTS = ['Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI']
MONO_FONTS = ['Cascadia Mono', 'Consolas']
ICON_FONTS = ['Segoe Fluent Icons', 'Segoe MDL2 Assets']


class Glyph:
    MORE = ''
    PIN = ''
    PINNED = ''
    SPEAKER = ''
    COPY = ''
    DOWN = ''


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    raised: str
    hover: str
    border: str
    border_strong: str
    text: str
    text2: str
    muted: str
    accent: str
    accent_hover: str
    on_accent: str
    live: str
    ok: str
    warn: str
    error: str

    def tone(self, tone):
        return {'live': self.live, 'ok': self.ok, 'warn': self.warn, 'error': self.error,
                'busy': self.accent}.get(tone, self.muted)


DARK = Palette(
    dark=True, bg='#101216', surface='#171A20', raised='#1E222A', hover='#252A33',
    border='#22262E', border_strong='#323844', text='#E9EBEF', text2='#9EA6B3', muted='#666E7C',
    accent='#8C9CFF', accent_hover='#A4B1FF', on_accent='#0B0D12',
    live='#FF6464', ok='#45D08F', warn='#F0B44C', error='#FF6B6B',
)

LIGHT = Palette(
    dark=False, bg='#FAFAF8', surface='#FFFFFF', raised='#F1F2F4', hover='#EAECEF',
    border='#E6E7EA', border_strong='#D3D6DC', text='#16181D', text2='#5A6170', muted='#8E949F',
    accent='#4C5FE6', accent_hover='#3B4ED6', on_accent='#FFFFFF',
    live='#E5484D', ok='#1E9E62', warn='#B97409', error='#D93A3F',
)


def rgba(hex_color, alpha):
    c = QColor(hex_color)
    return f'rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})'


def qcolor(hex_color, alpha=1.0):
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def font(families, px, weight=QFont.Weight.Normal):
    f = QFont()
    f.setFamilies(families)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def _icon(px, draw):
    icon = QIcon()
    for scale in (1, 2):
        pm = QPixmap(px * scale, px * scale)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        draw(p)
        p.end()
        icon.addPixmap(pm)
    return icon


def glyph_icon(glyph, color, px=16):
    def draw(p):
        p.setFont(font(ICON_FONTS, px))
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, glyph)
    return _icon(px, draw)


def shape_icon(shape, color, px=12):
    def draw(p):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        if shape == 'stop':
            p.drawRoundedRect(QRectF(1.5, 1.5, px - 3, px - 3), 2, 2)
        else:
            path = QPainterPath()
            path.moveTo(2.5, 1)
            path.lineTo(px - 1, px / 2)
            path.lineTo(2.5, px - 1)
            path.closeSubpath()
            p.drawPath(path)
    return _icon(px, draw)


def app_icon(accent):
    """Rounded tile with three caption lines; the last one is the live one."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 64
        tile = QPainterPath()
        tile.addRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 15 * s, 15 * s)
        p.fillPath(tile, QColor('#161922'))
        p.setPen(Qt.PenStyle.NoPen)
        for y, x1, color in ((22, 50, '#E9EBEF'), (32, 42, '#E9EBEF'), (42, 30, accent)):
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(14 * s, (y - 2.6) * s, (x1 - 14) * s, 5.2 * s), 2.6 * s, 2.6 * s)
        p.setBrush(QColor(accent))
        p.drawEllipse(QPointF(37 * s, 42 * s), 2.8 * s, 2.8 * s)
        p.end()
        icon.addPixmap(pm)
    return icon


def mark_pixmap(accent, px=22):
    return app_icon(accent).pixmap(px, px)


def qpalette(c):
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, color in ((roles.Window, qcolor(c.bg)), (roles.Base, qcolor(c.bg)), (roles.AlternateBase, qcolor(c.raised)),
                        (roles.Text, qcolor(c.text)), (roles.WindowText, qcolor(c.text)), (roles.ButtonText, qcolor(c.text)),
                        (roles.Button, qcolor(c.raised)), (roles.Highlight, qcolor(c.accent, 0.35)),
                        (roles.HighlightedText, qcolor(c.text)), (roles.PlaceholderText, qcolor(c.muted)),
                        (roles.ToolTipBase, qcolor(c.raised)), (roles.ToolTipText, qcolor(c.text))):
        pal.setColor(role, color)
    for role in (roles.Text, roles.ButtonText, roles.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, qcolor(c.muted))
    return pal


STYLE = Template('''
* { font-family: $ui; outline: none; }
QMainWindow, #root { background: $bg; }
QToolTip { background: $raised; color: $text; border: 1px solid $border_strong; border-radius: 6px; padding: 5px 8px; }

#topbar { background: $bg; border-bottom: 1px solid $border; }
#statusbar { background: $bg; border-top: 1px solid $border; }
#appTitle { color: $text; font-size: 14px; font-weight: 600; }
#statusText { color: $text2; font-size: 12px; }
#statusText[tone="error"] { color: $error; }
#statusText[tone="warn"] { color: $warn; }
#statusText[tone="ok"] { color: $ok; }
#note { color: $muted; font-size: 12px; }
#note:hover { color: $text2; }
#toast { color: $ok; font-size: 12px; font-weight: 600; }

#pill { background: $raised; border: 1px solid $border; border-radius: 12px; }
#pill[tone="live"] { background: $live_soft; border-color: $live_border; }
#pill[tone="ok"] { background: $ok_soft; border-color: $ok_border; }
#pill[tone="warn"] { background: $warn_soft; border-color: $warn_border; }
#pill[tone="error"] { background: $error_soft; border-color: $error_border; }
#pill[tone="busy"] { background: $accent_soft; border-color: $accent_border; }
#pillText { color: $text2; font-size: 12px; font-weight: 600; }
#pillText[tone="live"] { color: $live; }
#pillText[tone="ok"] { color: $ok; }
#pillText[tone="warn"] { color: $warn; }
#pillText[tone="error"] { color: $error; }
#pillText[tone="busy"] { color: $accent; }

QPushButton {
    background: $raised; color: $text; border: 1px solid $border; border-radius: 8px;
    padding: 0 12px; min-height: 30px; font-size: 13px;
}
QPushButton:hover { background: $hover; border-color: $border_strong; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $muted; }
QPushButton[kind="primary"] { background: $accent; color: $on_accent; border-color: $accent; font-weight: 600; padding: 0 16px; }
QPushButton[kind="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[kind="primary"]:disabled { background: $accent_soft; color: $muted; border-color: transparent; }
QPushButton[kind="danger"] { background: $live_soft; color: $live; border-color: $live_border; font-weight: 600; padding: 0 16px; }
QPushButton[kind="danger"]:hover { background: $live_soft_hover; }
QPushButton[kind="danger"]:disabled { color: $muted; background: transparent; border-color: $border; }
QPushButton[kind="icon"] { background: transparent; border: 1px solid transparent; padding: 0; min-width: 30px; max-width: 30px; }
QPushButton[kind="icon"]:hover { background: $hover; }
QPushButton[kind="icon"]:checked { background: $accent_soft; }
QPushButton[kind="icon"]::menu-indicator { image: none; width: 0; }
QPushButton[kind="floating"] {
    background: $raised; color: $text; border: 1px solid $border_strong; border-radius: 15px;
    padding: 0 14px; min-height: 30px; font-size: 12px; font-weight: 600;
}
QPushButton[kind="floating"]:hover { background: $hover; }
QPushButton[kind="bar"] { background: transparent; border: none; color: $accent; font-weight: 600; padding: 0 8px; min-height: 26px; font-size: 12px; }
QPushButton[kind="bar"]:hover { background: $accent_soft; }
QPushButton[kind="barquiet"] { background: transparent; border: none; color: $text2; padding: 0 8px; min-height: 26px; font-size: 12px; }
QPushButton[kind="barquiet"]:hover { background: $hover; }

#selbar { background: $raised; border: 1px solid $border_strong; border-radius: 17px; }
#selbarText { color: $text; font-size: 12px; font-weight: 600; padding-left: 6px; }

QComboBox#source {
    background: transparent; color: $text2; border: 1px solid transparent; border-radius: 8px;
    padding: 0 26px 0 8px; min-height: 30px; font-size: 12px;
}
QComboBox#source:hover { background: $hover; color: $text; }
QComboBox#source:disabled { color: $muted; }
QComboBox#source::drop-down { border: none; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }
QComboBox#source::down-arrow { image: url("$chevron"); width: 10px; height: 10px; }
QComboBox QAbstractItemView {
    background: $raised; color: $text; border: 1px solid $border_strong; border-radius: 10px; padding: 4px;
    selection-background-color: $accent_soft; selection-color: $text; outline: none;
}
QComboBox QAbstractItemView::item { min-height: 30px; padding: 0 10px; border-radius: 6px; border: none; }
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover { background: $accent_soft; color: $text; }
QComboBoxPrivateContainer { background: transparent; border: none; padding: 0; margin: 0; }

QTextEdit#transcript { background: $bg; border: none; color: $text;
    selection-background-color: $selection; selection-color: $text; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px 4px 0; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 3px; min-height: 36px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; height: 0; }
QScrollBar:horizontal { height: 0; }

QMenu { background: $raised; color: $text; border: 1px solid $border_strong; border-radius: 10px; padding: 5px; }
QMenu::item { padding: 7px 28px 7px 28px; border-radius: 6px; font-size: 13px; }
QMenu::right-arrow { image: url("$chevron_right"); width: 10px; height: 10px; right: 8px; }
QMenu::item:selected { background: $hover; }
QMenu::item:disabled { color: $muted; }
QMenu::indicator { width: 14px; height: 14px; left: 8px; }
QMenu::item:checked { color: $accent; font-weight: 600; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }

QMessageBox { background: $surface; }
QMessageBox QLabel { color: $text; font-size: 13px; }
''')


def stylesheet(c):
    chevron = ASSETS / ('chevron-dark.svg' if c.dark else 'chevron-light.svg')
    soft = 0.14 if c.dark else 0.10
    values = {f.name: getattr(c, f.name) for f in fields(c) if isinstance(getattr(c, f.name), str)}
    return STYLE.substitute(
        ui=', '.join(f'"{f}"' for f in UI_FONTS), chevron=chevron.as_posix(),
        chevron_right=(ASSETS / ('chevron-right-dark.svg' if c.dark else 'chevron-right-light.svg')).as_posix(),
        selection=rgba(c.accent, 0.38 if c.dark else 0.26),
        accent_soft=rgba(c.accent, soft), accent_border=rgba(c.accent, 0.45),
        live_soft=rgba(c.live, soft), live_soft_hover=rgba(c.live, soft + 0.08), live_border=rgba(c.live, 0.40),
        ok_soft=rgba(c.ok, soft), ok_border=rgba(c.ok, 0.40),
        warn_soft=rgba(c.warn, soft), warn_border=rgba(c.warn, 0.40),
        error_soft=rgba(c.error, soft), error_border=rgba(c.error, 0.40),
        **values,
    )


def tint_title_bar(widget, c):
    """Windows 11: native title bar in the window's own background colour."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        hwnd = int(widget.winId())

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(hex_color):
            q = QColor(hex_color)
            return q.red() | (q.green() << 8) | (q.blue() << 16)

        set_attr(20, 1 if c.dark else 0)   # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_attr(35, colorref(c.bg))       # DWMWA_CAPTION_COLOR
        set_attr(36, colorref(c.text2))    # DWMWA_TEXT_COLOR
    except Exception:
        pass
