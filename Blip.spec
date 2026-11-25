# PyInstaller runs this spec on the target operating system.
import os
from pathlib import Path
import sys

project = Path(SPECPATH)
assets = project / ".build-assets"
version = os.environ.get("BLIP_BUILD_VERSION", "0.1.0")

if sys.platform == "win32":
    backends = ["pystray._win32"]
    icon = str(assets / "blip.ico")
elif sys.platform == "darwin":
    backends = ["pystray._darwin"]
    icon = str(assets / "blip.icns")
else:
    backends = ["pystray._xorg"]
    icon = None

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[],
    hiddenimports=backends,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "test_main"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Blip",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
    version=str(assets / "windows-version.txt") if sys.platform == "win32" else None,
)
distribution = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Blip")
if sys.platform == "darwin":
    app = BUNDLE(
        distribution,
        name="Blip.app",
        icon=icon,
        bundle_identifier="io.github.TahashiburRahman.blip",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Copyright 2026 Tahashibur Rahman. MIT license.",
        },
    )
