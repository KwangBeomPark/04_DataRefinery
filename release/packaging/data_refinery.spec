# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import re


project_root = Path(SPECPATH).parents[1]
app_source = project_root / 'src' / 'data_refinery.py'
source = app_source.read_text(encoding='utf-8')
match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
if match is None:
    raise RuntimeError('Could not find __version__ in data_refinery.py')
release_executable_name = f"App04_DataRefinery_v{match.group(1)}"

a = Analysis(
    [str(app_source)],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / 'assets'), 'assets'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
