"""Linger: live English captions that stay put long enough to catch.

Importing never starts capture or networking.
"""
import os
import sys

# Preload System32's ICU before Qt, so Anaconda or other DLLs on PATH cannot break PySide6.
if sys.platform == 'win32':
    import ctypes
    system32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
    for dll_name in ('icuuc.dll', 'icuin.dll'):
        dll_path = os.path.join(system32, dll_name)
        if os.path.isfile(dll_path):
            try:
                ctypes.windll.kernel32.LoadLibraryW(dll_path)
            except Exception:
                pass
