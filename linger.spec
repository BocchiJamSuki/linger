# PyInstaller build: `pyinstaller --noconfirm linger.spec` → dist/Linger/Linger.exe
from PyInstaller.utils.hooks import collect_data_files

unused_qt = ['Qt3DAnimation', 'Qt3DCore', 'Qt3DExtras', 'Qt3DInput', 'Qt3DLogic', 'Qt3DRender', 'QtBluetooth',
             'QtCharts', 'QtDataVisualization', 'QtGraphs', 'QtMultimedia', 'QtMultimediaWidgets', 'QtNfc',
             'QtOpenGL', 'QtOpenGLWidgets', 'QtPdf', 'QtPdfWidgets', 'QtPositioning', 'QtQml', 'QtQuick',
             'QtQuick3D', 'QtQuickControls2', 'QtQuickWidgets', 'QtRemoteObjects', 'QtSensors', 'QtSerialPort',
             'QtSql', 'QtTest', 'QtWebChannel', 'QtWebEngineCore', 'QtWebEngineWidgets', 'QtWebSockets', 'QtXml']

a = Analysis(
    ['main.py'],
    datas=[('linger/assets', 'linger/assets')] + collect_data_files('soundcard'),  # soundcard reads a C header at import.
    hiddenimports=['_cffi_backend'],
    excludes=[f'PySide6.{name}' for name in unused_qt] + ['tkinter', 'pytest'],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Linger', console=False,
          icon='linger/assets/linger.ico')
coll = COLLECT(exe, a.binaries, a.datas, name='Linger')
