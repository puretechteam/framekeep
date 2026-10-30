# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
# Builds a single portable FrameKeep.exe with FFmpeg inside:  pyinstaller framekeep.spec
# FFmpeg comes from ./ffmpeg (tools/fetch_ffmpeg.py) unless FRAMEKEEP_FFMPEG_DIR points elsewhere. It is the minimal
# build from tools/build_ffmpeg.sh, so the one-file EXE stays small and unpacks quickly on every launch.

import os
from PyInstaller.utils.hooks import collect_data_files

ff_dir = os.environ.get("FRAMEKEEP_FFMPEG_DIR", "ffmpeg")
ff_files = [f for f in os.listdir(ff_dir) if f.lower().endswith((".exe", ".dll"))]
if "ffmpeg.exe" not in ff_files:
    raise SystemExit(f"ffmpeg.exe not found in {ff_dir}. Run: python tools/fetch_ffmpeg.py")

binaries = [(os.path.join(ff_dir, f), "ffmpeg") for f in ff_files]
datas = collect_data_files("customtkinter") + [
    ("assets/icon.ico", "assets"),
    ("LICENSE", "."),
]
for name in ("LICENSE", "BUILDINFO.txt"):  # BUILDINFO.txt: configure line and library versions (tools/build_ffmpeg.sh)
    if os.path.exists(os.path.join(ff_dir, name)):
        datas.append((os.path.join(ff_dir, name), "ffmpeg"))

a = Analysis(
    ["FrameKeep.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    excludes=["numpy", "cv2", "PySide6", "matplotlib", "pandas", "scipy", "IPython", "pytest"],
)
# PyInstaller's dependency scan of ffmpeg.exe also copies the FFmpeg DLLs into the bundle root, a second unused copy
# of the whole build. Only the ffmpeg\ folder is used.
ff_lower = {f.lower() for f in ff_files}
a.binaries = [b for b in a.binaries if b[0].lower() not in ff_lower]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="FrameKeep",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
