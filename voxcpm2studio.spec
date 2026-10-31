# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

    pyinstaller voxcpm2studio.spec --noconfirm

产物：dist/VoxCPM2Studio/VoxCPM2Studio.exe
模型权重不打进去（首次启动在界面里下载到 exe 同级的 models/）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
ASSETS = ROOT / "voxcpm_studio" / "assets"

datas = [(str(ASSETS), "voxcpm_studio/assets")]
# voxcpm 的 tokenizer 脚本 / 配置在包里是数据文件，要带上
datas += collect_data_files("voxcpm", include_py_files=False)

hiddenimports = collect_submodules("voxcpm") + [
    "soundfile",
    "PyQt6.QtSvg",
    "PyQt6.QtMultimedia",
]

# 明确排掉用不到的大件，能省几百 MB
excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQuick",
    "PyQt6.QtQml",
    "PyQt6.Qt3DCore",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoxCPM2Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                      # 桌面软件：不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "app.ico") if (ASSETS / "app.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoxCPM2Studio",
)
