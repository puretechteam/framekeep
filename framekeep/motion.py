# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Motion detection on a camera's small analysis frames (engine.ANALYSIS_W x ANALYSIS_H RGB), and the per-day
motion event log next to the camera's recordings.

No NumPy: each frame is cut down with bytes slicing (which runs in C) to a coarse grid of green values, about 1300
cells, so the Python part stays a few thousand operations per frame."""

import json, os, threading, time

STEP = 4             # sample every 4th pixel across and down
NOISE = 14           # a cell must differ from the background by this much (0-255) to count as changed
# Share of the grid that must change, for sensitivity 1 (least sensitive) to 10. Tuned on the docs/perf webcam
# sample: its sensor noise never passed 0.7%, and a person-sized object walking slowly across scored 4.8%, so the
# default (5) sits at 2.5%.
THRESHOLDS = (0.12, 0.08, 0.055, 0.04, 0.025, 0.018, 0.013, 0.010, 0.007, 0.005)
START_FRAMES = 2     # this many changed frames in a row start an event (1 for keyframe-only cameras)
END_AFTER = 5.0      # seconds without motion that end an event
LEARN = 0.08         # how fast the background follows slow changes (light, shadows)

def grid(frame, w, h):
    """Green channel sampled every STEP pixels, as a bytes object of (w // STEP) * (h // STEP) values."""
    green = frame[1::3]
    return b"".join(green[y * w:(y + 1) * w:STEP] for y in range(0, h, STEP))

class MotionDetector:
    """Feed it frames with their wall-clock time; it calls on_start(t) and on_end(start, end, peak)."""
    def __init__(self, w, h, sensitivity=5, start_frames=START_FRAMES, on_start=None, on_end=None):
        self.w, self.h = w, h
        self.set_sensitivity(sensitivity)
        self.start_frames = start_frames
        self.on_start, self.on_end = on_start or (lambda t: None), on_end or (lambda s, e, p: None)
        self.bg = None
        self.prev = None
        self.run = 0
        self.active = None        # [start, last motion, peak] while an event is open
        self.level = 0.0          # share of the grid that changed in the last frame

    def set_sensitivity(self, sensitivity):
        self.threshold = THRESHOLDS[min(10, max(1, int(sensitivity))) - 1]

    def feed(self, frame, t):
        cells = grid(frame, self.w, self.h)
        if self.bg is None or len(self.bg) != len(cells):
            self.bg, self.prev = [float(c) for c in cells], cells
            return False
        bg, prev = self.bg, self.prev
        changed = 0
        for i, c in enumerate(cells):
            d = c - bg[i]
            # moving = different from the background AND from the last frame. The second test ignores the "ghost" a
            # departed object leaves in the background while it fades out, which otherwise kept events going ~7 s
            if (d > NOISE or d < -NOISE) and abs(c - prev[i]) > NOISE:
                changed += 1
            bg[i] += d * LEARN
        self.prev = cells
        self.level = changed / len(cells)
        moving = self.level >= self.threshold
        self.run = self.run + 1 if moving else 0
        if self.active:
            if moving:
                self.active[1] = t
                self.active[2] = max(self.active[2], self.level)
            elif t - self.active[1] >= END_AFTER:
                self.close()
        elif self.run >= self.start_frames:
            self.active = [t, t, self.level]
            self.on_start(t)
        return bool(self.active)

    def close(self):
        """End an open event now (camera stopping)."""
        if self.active:
            start, last, peak = self.active
            self.active = None
            self.on_end(start, last, peak)

class EventLog:
    """Appends finished motion events to <camera folder>/<YYYY-MM-DD>/events.jsonl, one JSON object per line:
    {"start": epoch seconds, "end": epoch seconds, "peak": share of the picture that changed}."""
    NAME = "events.jsonl"

    def __init__(self, folder):
        self.folder = folder
        self._lock = threading.Lock()

    def path_for(self, t):
        return os.path.join(self.folder, time.strftime("%Y-%m-%d", time.localtime(t)), self.NAME)

    def add(self, start, end, peak):
        path = self.path_for(start)
        line = json.dumps({"start": round(start, 2), "end": round(end, 2), "peak": round(peak, 3)})
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

def read_events(day_folder):
    """Events of one camera day, oldest first. Damaged lines (a crash mid-write) are skipped."""
    out = []
    try:
        with open(os.path.join(day_folder, EventLog.NAME), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    out.append({"start": float(e["start"]), "end": float(e["end"]), "peak": float(e.get("peak", 0))})
                except (ValueError, KeyError, TypeError):
                    pass
    except OSError:
        pass
    return sorted(out, key=lambda e: e["start"])

def chunk_start(path):
    """Wall-clock start of a camera chunk from its folder and file name (<YYYY-MM-DD>/<HH-MM-SS>.ext), or None."""
    day = os.path.basename(os.path.dirname(path))
    stem = os.path.splitext(os.path.basename(path))[0][:8]
    try:
        return time.mktime(time.strptime(f"{day} {stem}", "%Y-%m-%d %H-%M-%S"))
    except ValueError:
        return None

def locate(event_start, chunks):
    """(chunk path, offset in seconds) of the chunk an event starts in. chunks: paths of that day's files."""
    best = None
    for path in chunks:
        s = chunk_start(path)
        if s is not None and s <= event_start + 1 and (best is None or s > best[1]):
            best = (path, s)
    return (best[0], max(0.0, event_start - best[1])) if best else (None, 0.0)
