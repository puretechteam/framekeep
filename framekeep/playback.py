# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""What the player plays: the desktop and each camera as sources, a day of their chunks, layouts and clip names.
No Tk here, so it can be tested on its own."""

import os, re, time
from . import motion, stats
from .config import camera_root

DESKTOP = "desktop"
MAX_TILES = 4

def sources(cfg):
    """[(id, name, folder)] of everything that records: the desktop first, then each camera."""
    out = [(DESKTOP, "Desktop", cfg["log_root"])] if cfg.get("log_root") else []
    root = camera_root(cfg)
    if root:
        out += [(c["id"], c["name"], os.path.join(root, c["folder"])) for c in cfg["cameras"]]
    return out

def day_files(folder, day):
    """A day's chunks of one source, oldest first."""
    return [c["path"] for c in reversed(stats.list_chunks(os.path.join(folder, day)))] if folder else []

def days(cfg):
    """Days any source has footage for, newest first."""
    found = set()
    for _, _, folder in sources(cfg):
        found.update(name for name, path in stats.list_days(folder) if stats.list_chunks(path))
    return sorted(found, reverse=True)

def locate(cfg, path):
    """(source id, day) of a recording path, or (None, None) when it isn't in a FrameKeep folder."""
    day_dir = os.path.dirname(os.path.abspath(path))
    folder, day = os.path.dirname(day_dir), os.path.basename(day_dir)
    for sid, _, root in sources(cfg):
        if root and os.path.normcase(os.path.abspath(root)) == os.path.normcase(folder):
            return sid, day
    return None, None

def wall_time(path, offset=0.0):
    """Wall-clock time `offset` seconds into a chunk, from its folder and name; None if the name holds no time."""
    start = motion.chunk_start(path)
    return None if start is None else start + max(0.0, offset)

def day_bounds(day):
    """(midnight, next midnight) of a YYYY-MM-DD day as epoch seconds."""
    t0 = time.mktime(time.strptime(day, "%Y-%m-%d"))
    return t0, t0 + 86400

def tiles(n):
    """Grid cells (row, column, rowspan, columnspan) for n tiles; the first (main) one is the biggest."""
    if n <= 1:
        return [(0, 0, 1, 2)]
    if n == 2:
        return [(0, 0, 1, 1), (0, 1, 1, 1)]
    if n == 3:
        return [(0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1)]
    return [(i // 2, i % 2, 1, 1) for i in range(min(n, MAX_TILES))]

def x_for(t, t0, t1, width):
    return (t - t0) / max(1e-9, t1 - t0) * width

def t_for(x, t0, t1, width):
    return t0 + min(max(x, 0), width) / max(1, width) * (t1 - t0)

def clip_name(source_name, t, ext=".mp4"):
    """Default file name for a clip starting at wall time t, e.g. 'Front door 2026-10-30 14-02-08.mp4'."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", source_name).strip(" .") or "Clip"
    return f"{safe} {time.strftime('%Y-%m-%d %H-%M-%S', time.localtime(t))}{ext}"

def motion_events(cfg, ids, day):
    """Motion events of the chosen cameras on a day, sorted by start: [(start, end, camera name)]."""
    names = {sid: (name, folder) for sid, name, folder in sources(cfg) if sid != DESKTOP}
    out = []
    for sid in ids:
        if sid in names:
            name, folder = names[sid]
            out += [(e["start"], e["end"], name) for e in motion.read_events(os.path.join(folder, day))]
    return sorted(out)

def next_event(events, position, direction):
    """Start of the next (direction 1) or previous (-1) event from position, or None."""
    if direction > 0:
        return next((s for s, _, _ in events if s > position + 0.5), None)
    return next((s for s, _, _ in reversed(events) if s < position - 1.0), None)
