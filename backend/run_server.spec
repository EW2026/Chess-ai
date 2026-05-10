# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is the directory containing this spec file (backend/).
# Stockfish lives one level up in <project_root>/stockfish/.
# Select the correct binary for the build platform.
import sys as _sys
_sf_name  = 'stockfish-windows-x86-64-avx2.exe' if _sys.platform == 'win32' else 'stockfish-linux-x86-64-avx2'
_stockfish = os.path.normpath(os.path.join(SPECPATH, '..', 'stockfish', _sf_name))

datas = [('templates', 'templates'), ('static', 'static')]
binaries = [(_stockfish, '.')]
hiddenimports = ['whitenoise', 'whitenoise.middleware', 'whitenoise.storage', 'waitress', 'waitress.server', 'waitress.task', 'waitress.channel', 'psutil']
tmp_ret = collect_all('django')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rest_framework')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('corsheaders')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Collect all api submodules (models, migrations, views, etc.) so Django's
# dynamic migration loader can find every migration file at runtime.
hiddenimports += collect_submodules('api')


a = Analysis(
    ['run_server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='run_server',
)
