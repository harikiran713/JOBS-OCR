# PyInstaller spec for the processing engine. Build: pyinstaller mmi-backend.spec --noconfirm
# RapidOCR's ONNX models are bundled so the installed application never downloads them.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("rapidocr", "pymupdf", "onnxruntime", "openpyxl"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("uvicorn") + collect_submodules("mmi")

a = Analysis(
    ["run_backend.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "paddle", "paddleocr"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="mmi-backend", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="mmi-backend", upx=False)
