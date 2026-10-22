# PyInstaller-spec for PDF til Markdown.app. Kjøres via build.sh, som setter miljøvariablene.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
TESSDATA = os.environ["TESSDATA_DIR"]
VERSION = os.environ.get("APP_VERSION", "1.0.0")
BUNDLE_ID = os.environ.get("BUNDLE_ID", "io.github.foffster.pdf-til-markdown")
ICON = ROOT / "packaging" / "icon.icns"

datas, binaries, hiddenimports = [], [], []
# pymupdf-layout installeres inn i pymupdf/, så den dekkes av "pymupdf"
for pkg in ("pymupdf4llm", "pymupdf"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += [(TESSDATA, "tessdata")]

a = Analysis(
    [str(ROOT / "app.py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF til Markdown",
    console=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="PDF til Markdown")
app = BUNDLE(
    coll,
    name="PDF til Markdown.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "PDF til Markdown",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHumanReadableCopyright": "MIT-lisens",
    },
)
