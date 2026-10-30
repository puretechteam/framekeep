# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import os, sys
from . import APP_NAME

# PyInstaller unpacks bundled data to sys._MEIPASS; from source, the repo root holds ffmpeg/ and assets/
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FFMPEG_DIR = os.path.join(BASE_DIR, "ffmpeg")
ICON_PATH = os.path.join(BASE_DIR, "assets", "icon.ico")

APPDATA_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), APP_NAME)
CONFIG_FILE = os.path.join(APPDATA_DIR, "config.json")
BENCHMARK_FILE = os.path.join(APPDATA_DIR, "benchmark.json")
BENCHMARK_DIR = os.path.join(APPDATA_DIR, "benchmark")
THUMB_DIR = os.path.join(APPDATA_DIR, "thumbs")
LOG_FILE = os.path.join(APPDATA_DIR, "framekeep.log")
os.makedirs(APPDATA_DIR, exist_ok=True)
