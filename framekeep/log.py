# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import datetime, logging, logging.handlers, os, sys, threading
from .paths import LOG_FILE

COLORS = {"RED": "\x1b[31m", "GREEN": "\x1b[32m", "CYAN": "\x1b[36m", "YELLOW": "\x1b[33m", "MAGENTA": "\x1b[35m", "RESET": "\x1b[0m"}

# Subscribers (the GUI log view) get every line as (timestamp, tag, message, color)
_listeners = []
_history = []
_lock = threading.Lock()
_file = logging.getLogger("framekeep")
_file.setLevel(logging.INFO)
_file.propagate = False
try:
    _handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _file.addHandler(_handler)
except OSError:
    pass

if sys.platform == "win32":
    os.system("")  # enables ANSI colors in the Windows console

def ts():
    return datetime.datetime.now().strftime("%m/%d/%y %H:%M:%S")

def log(tag, msg, color="RESET"):
    stamp = ts()
    if sys.stdout:
        try:
            print(f"{stamp} {COLORS[color]}[{tag}]{COLORS['RESET']} {msg}", flush=True)
        except (OSError, ValueError):
            pass
    _file.info(f"{stamp} [{tag}] {msg}")
    with _lock:
        _history.append((stamp, tag, msg, color))
        del _history[:-500]
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(stamp, tag, msg, color)
        except Exception:
            pass

def subscribe(fn):
    """Register fn and replay recent history to it."""
    with _lock:
        _listeners.append(fn)
        history = list(_history)
    for line in history:
        fn(*line)
