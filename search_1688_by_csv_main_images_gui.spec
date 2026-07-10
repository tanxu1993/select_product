# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(__file__).resolve().parent
hiddenimports = (
    collect_submodules("config")
    + collect_submodules("playwright")
    + collect_submodules("pandas")
    + collect_submodules("openpyxl")
    + collect_submodules("ozon_selection")
    + collect_submodules("scripts")
)

datas = [
    (str(project_root / "config"), "config"),
    (str(project_root / "src" / "ozon_selection"), "ozon_selection"),
    (str(project_root / "docs" / "sqlite_schema.sql"), "docs"),
]

pathex = [
    str(project_root),
    str(project_root / "src"),
]


a = Analysis(
    ["scripts/search_1688_by_csv_main_images_gui.py"],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    exclude_binaries=True,
    name="search_1688_by_csv_main_images_gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="search_1688_by_csv_main_images_gui",
)
