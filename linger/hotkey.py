"""System-wide hotkey (works while another application has focus) via Win32 RegisterHotKey."""
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_OEM_3, VK_F8, VK_F9 = 0xC0, 0x77, 0x78
WM_HOTKEY = 0x0312

# Two keys at most, avoiding common conferencing shortcuts (e.g. Zoom uses Alt+letter; Alt+Q ends a meeting).
COMBOS = {
    'Ctrl+`': (MOD_CONTROL, VK_OEM_3),
    'Alt+`': (MOD_ALT, VK_OEM_3),
    'F8': (0, VK_F8),
    'F9': (0, VK_F9),
}
DEFAULT = 'Ctrl+`'


class GlobalHotkey(QAbstractNativeEventFilter):
    ID = 0x4C43

    def __init__(self, app, callback):
        super().__init__()
        self.callback = callback
        self.combo = None
        if sys.platform == 'win32':
            app.installNativeEventFilter(self)

    def register(self, combo):
        """Returns True when `combo` is active (or None was asked for)."""
        self.unregister()
        if combo is None or sys.platform != 'win32' or combo not in COMBOS:
            return combo is None
        import ctypes
        mods, vk = COMBOS[combo]
        if ctypes.windll.user32.RegisterHotKey(None, self.ID, mods | MOD_NOREPEAT, vk):
            self.combo = combo
            return True
        return False

    def unregister(self):
        if self.combo and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
        self.combo = None

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b'windows_generic_MSG' and self.combo:
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.ID:
                QTimer.singleShot(0, self.callback)
                return True, 0
        return False, 0
