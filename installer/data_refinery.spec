# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import re
from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).parent
app_source = project_root / 'src' / 'data_refinery.py'
source = (project_root / 'src' / 'version.py').read_text(encoding='utf-8')
match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
if match is None:
    raise RuntimeError('Could not find __version__ in version.py')
release_executable_name = f"App04_DataRefinery_v{match.group(1)}"

duckdb_datas, duckdb_binaries, duckdb_hiddenimports = collect_all('duckdb')

a = Analysis(
    [str(app_source)],
    pathex=[str(project_root)],
    binaries=duckdb_binaries,
    datas=[
        (str(project_root / 'assets'), 'assets'),
    ] + duckdb_datas,
    hiddenimports=[
        'duckdb',
        'win32com',
        'win32com.client',
        'pythoncom',
        'pywintypes',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ] + duckdb_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtPositioning',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name=release_executable_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / 'assets' / 'icons' / 'icon.ico'),
    exclude_binaries=True,
)

# Install all dependencies beside the launcher.  The installed application then
# starts directly from Local AppData instead of extracting a one-file bundle on
# every launch.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=release_executable_name,
)
