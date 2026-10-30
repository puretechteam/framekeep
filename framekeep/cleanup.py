# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Storage: the "recordings are getting big" warning, and the optional deletion of old camera footage.

Deletion is off unless the user turns on "Delete old camera footage", and it only ever touches camera chunks:
files FrameKeep named itself (HH-MM-SS.ext) in <camera root>/<configured camera's folder>/<YYYY-MM-DD>/. Desktop
recordings are never deleted, moved or changed. Cleanup refuses to run at all if the desktop recordings folder is
the camera folder or inside it, and it never deletes a file that is still being recorded."""

import os, re, time
from .config import camera_root
from .log import log
from .motion import chunk_start, read_events
from .stats import DAY_RE, human_bytes, library_summary

CHUNK_RE = re.compile(r"^\d{2}-\d{2}-\d{2}(-\d+)?\.(mp4|mkv|webm)$", re.IGNORECASE)
DAY = 86400

def _norm(path):
    return os.path.normcase(os.path.abspath(path))

def _inside(path, folder):
    """True if path is folder or anywhere below it."""
    path, folder = _norm(path), _norm(folder)
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:      # different drives
        return False

def camera_folders(cfg):
    """The folders cleanup may look in: one per configured camera, and only if none of them holds the desktop
    recordings folder."""
    root = camera_root(cfg)
    if not root or not os.path.isdir(root):
        return []
    folders = [os.path.join(root, c["folder"]) for c in cfg["cameras"] if c.get("folder")]
    desktop = cfg.get("log_root")
    if desktop and (_inside(desktop, root) or any(_inside(desktop, f) for f in folders)):
        return []
    return [f for f in folders if os.path.isdir(f)]

def plan(cfg, now=None, active=()):
    """[(path, size)] of camera chunks old enough to delete under cfg's limits. Quiet chunks go after
    camera_keep_days; chunks that overlap a motion event are kept for camera_keep_motion_days."""
    now = now or time.time()
    quiet_age, motion_age = cfg["camera_keep_days"] * DAY, cfg["camera_keep_motion_days"] * DAY
    skip = {_norm(p) for p in active}
    out = []
    for cam_folder in camera_folders(cfg):
        for day in sorted(os.listdir(cam_folder)):
            day_path = os.path.join(cam_folder, day)
            if not DAY_RE.match(day) or not os.path.isdir(day_path):
                continue
            events = None
            for e in os.scandir(day_path):
                if not e.is_file() or not CHUNK_RE.match(e.name) or _norm(e.path) in skip:
                    continue
                st = e.stat()
                end = st.st_mtime
                age = now - end
                if age < quiet_age or now - st.st_mtime < 3600:     # never anything written in the last hour
                    continue
                if age < motion_age:
                    if events is None:
                        events = read_events(day_path)
                    start = chunk_start(e.path) or end
                    if any(ev["start"] <= end and ev["end"] >= start for ev in events):
                        continue
                out.append((e.path, st.st_size))
    return out

def run(cfg, active=()):
    """Delete what plan() lists (only when the user turned cleanup on). Returns (files deleted, bytes freed)."""
    if not cfg.get("camera_cleanup"):
        return 0, 0
    count = freed = 0
    for path, size in plan(cfg, active=active):
        try:
            os.remove(path)
        except OSError as e:
            log("WARN", f"Could not delete old camera footage {path}: {e}", "YELLOW")
            continue
        count += 1
        freed += size
        log("CLEANUP", f"Deleted old camera footage: {path}", "CYAN")
    if count:
        log("CLEANUP", f"Freed {human_bytes(freed)} ({count} old camera file{'s' if count != 1 else ''})", "GREEN")
    return count, freed

def usage(cfg):
    """Bytes of recordings FrameKeep wrote: desktop day folders plus the camera folder."""
    total = library_summary(cfg["log_root"])["total"] if cfg.get("log_root") else 0
    root = camera_root(cfg)
    for cam in cfg["cameras"] if root else ():
        folder = os.path.join(root, cam["folder"])
        if os.path.isdir(folder):
            total += library_summary(folder)["total"]   # same day-folder layout, so finished days stay cached
    return total
