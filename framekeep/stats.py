# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Read-only views of the recordings folder. Nothing here writes inside it."""

import datetime, hashlib, json, os, re, threading
from .paths import APPDATA_DIR, THUMB_DIR

VIDEO_EXT = (".mp4", ".mkv", ".webm", ".avi")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROBE_CACHE = os.path.join(APPDATA_DIR, "probe_cache.json")
THUMB_CAP_MB = 50

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

def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"

_day_totals = {}   # day folder -> (folder mtime, skipped files in it, bytes, file count)

def day_totals(path, skip):
    """(bytes, files) for one day folder, minus the files in skip. Finished days are cached until the folder
    changes (adding, removing or renaming a file updates its mtime), so the dashboard doesn't rescan years of
    recordings every 30 seconds."""
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return 0, 0
    mine = frozenset(f for f in skip if os.path.dirname(f) == os.path.normcase(os.path.abspath(path)))
    hit = _day_totals.get(path)
    if hit and hit[0] == mtime and hit[1] == mine:
        return hit[2], hit[3]
    chunks = [c for c in list_chunks(path) if os.path.normcase(os.path.abspath(c["path"])) not in mine]
    size = sum(c["size"] for c in chunks)
    _day_totals[path] = (mtime, mine, size, len(chunks))
    return size, len(chunks)

def library_summary(root, exclude=()):
    """Totals for the recordings folder. Files in exclude (being written right now) are left out."""
    days = list_days(root)
    today = datetime.date.today().strftime("%Y-%m-%d")
    total = files = today_bytes = today_files = 0
    skip = {os.path.normcase(os.path.abspath(p)) for p in exclude}
    for name, path in days:
        if name == today:   # files here can still be growing without the folder changing
            chunks = [c for c in list_chunks(path) if os.path.normcase(os.path.abspath(c["path"])) not in skip]
            size, count = sum(c["size"] for c in chunks), len(chunks)
        else:
            size, count = day_totals(path, skip)
        total += size
        files += count
        if name == today:
            today_bytes, today_files = size, count
    return {"total": total, "files": files, "days": len(days), "today": today_bytes, "today_files": today_files}

def cache_key(chunk):
    return f"{chunk['path']}|{chunk['size']}|{int(chunk['mtime'])}"

def thumb_path(chunk):
    return os.path.join(THUMB_DIR, hashlib.sha1(cache_key(chunk).encode()).hexdigest() + ".jpg")

def prune_thumbs(max_mb=THUMB_CAP_MB, folder=THUMB_DIR):
    """Keep FrameKeep's own thumbnail cache (in AppData) under max_mb by removing the least recently used ones.
    Only .jpg files directly inside the cache folder are considered; recordings are never involved."""
    try:
        entries = [e for e in os.scandir(folder) if e.is_file(follow_symlinks=False) and e.name.lower().endswith(".jpg")]
    except OSError:
        return 0
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)   # newest (most recently used) first
    budget, removed = max_mb * 2**20, 0
    for e in entries:
        size = e.stat().st_size
        if budget >= size:
            budget -= size
            continue
        try:
            os.remove(os.path.join(folder, e.name))
            removed += 1
        except OSError:
            pass
    return removed

def touch(path):
    """Mark a cached thumbnail as used, so pruning keeps it."""
    try:
        os.utime(path)
    except OSError:
        pass

class ProbeCache:
    """ffprobe results keyed by path+size+mtime, stored in AppData. Writes are batched: opening a day with 40
    recordings used to rewrite the whole file 40 times."""
    FLUSH_DELAY = 3.0

    def __init__(self, path=PROBE_CACHE):
        self.path = path
        self._lock = threading.Lock()
        self._timer = None
        try:
            with open(path, encoding="utf-8") as f:
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
            if self._timer is None:
                self._timer = threading.Timer(self.FLUSH_DELAY, self.flush)
                self._timer.daemon = True
                self._timer.start()

    def flush(self):
        with self._lock:
            if self._timer is None:
                return      # nothing new since the last write
            self._timer.cancel()
            self._timer = None
            data = json.dumps(self._data)
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except OSError:
            pass
