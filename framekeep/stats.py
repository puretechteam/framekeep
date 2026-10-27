# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License
"""Read-only views of the recordings folder. Nothing here writes inside it."""

import datetime, hashlib, json, os, re, threading
from .paths import APPDATA_DIR, THUMB_DIR

VIDEO_EXT = (".mp4", ".mkv", ".webm", ".avi")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROBE_CACHE = os.path.join(APPDATA_DIR, "probe_cache.json")

def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024

def human_duration(seconds):
    seconds = int(seconds or 0)
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"

def list_days(root):
    """Day folders, newest first, as (name, path)."""
    if not root or not os.path.isdir(root):
        return []
    days = [(d, os.path.join(root, d)) for d in os.listdir(root) if DAY_RE.match(d) and os.path.isdir(os.path.join(root, d))]
    return sorted(days, reverse=True)

def list_chunks(day_path):
    """Recordings in a day folder, newest first."""
    out = []
    try:
        entries = list(os.scandir(day_path))
    except OSError:
        return out
    for e in entries:
        if e.is_file() and e.name.lower().endswith(VIDEO_EXT):
            st = e.stat()
            out.append({"path": e.path, "name": e.name, "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda c: c["name"], reverse=True)

def folder_bytes(path):
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            if f.lower().endswith(VIDEO_EXT):
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    return total

def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"

def library_summary(root, exclude=()):
    """Totals for the recordings folder. Files in exclude (being written right now) are left out."""
    days = list_days(root)
    today = datetime.date.today().strftime("%Y-%m-%d")
    total = files = today_bytes = today_files = 0
    skip = {os.path.normcase(os.path.abspath(p)) for p in exclude}
    for name, path in days:
        chunks = [c for c in list_chunks(path) if os.path.normcase(os.path.abspath(c["path"])) not in skip]
        size = sum(c["size"] for c in chunks)
        total += size
        files += len(chunks)
        if name == today:
            today_bytes, today_files = size, len(chunks)
    return {"total": total, "files": files, "days": len(days), "today": today_bytes, "today_files": today_files}

def cache_key(chunk):
    return f"{chunk['path']}|{chunk['size']}|{int(chunk['mtime'])}"

def thumb_path(chunk):
    return os.path.join(THUMB_DIR, hashlib.sha1(cache_key(chunk).encode()).hexdigest() + ".jpg")

class ProbeCache:
    """ffprobe results keyed by path+size+mtime, stored in AppData."""
    def __init__(self):
        self._lock = threading.Lock()
        try:
            with open(PROBE_CACHE, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, ValueError):
            self._data = {}

    def get(self, chunk):
        with self._lock:
            return self._data.get(cache_key(chunk))

    def put(self, chunk, info):
        with self._lock:
            self._data[cache_key(chunk)] = info
            if len(self._data) > 5000:
                for k in list(self._data)[:1000]:
                    del self._data[k]
            try:
                with open(PROBE_CACHE, "w", encoding="utf-8") as f:
                    json.dump(self._data, f)
            except OSError:
                pass
