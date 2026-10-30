# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import ctypes, threading
from ctypes import wintypes
from .log import log

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
KEYS = {**{f"f{i}": 0x6F + i for i in range(1, 25)}, **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
        **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
        "space": 0x20, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E, "pageup": 0x21, "pagedown": 0x22,
        "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C}

def parse_hotkey(text):
    """'ctrl+alt+f9' -> (modifiers, virtual key). Raises ValueError for anything unparseable."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            raise ValueError(f"unknown modifier '{p}'")
        mods |= MODS[p]
    if parts[-1] not in KEYS:
        raise ValueError(f"unknown key '{parts[-1]}'")
    if not mods and not parts[-1].startswith("f"):
        raise ValueError("hotkeys need a modifier unless they use an F key")
    return mods, KEYS[parts[-1]]

class Hotkeys:
    """System-wide hotkeys via RegisterHotKey on a dedicated message-loop thread."""
    def __init__(self):
        self._thread = None
        self._thread_id = None

    def set(self, bindings):
        """bindings: {"ctrl+alt+f9": callback, ...}. Replaces any previous registration."""
        self.stop()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(bindings, ready), name="hotkeys", daemon=True)
        self._thread.start()
        ready.wait(2)

    def stop(self):
        if self._thread and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = self._thread_id = None

    def _loop(self, bindings, ready):
        self._thread_id = kernel32.GetCurrentThreadId()
        callbacks = {}
        for i, (text, fn) in enumerate(bindings.items(), start=1):
            if not text:
                continue
            try:
                mods, vk = parse_hotkey(text)
            except ValueError as e:
                log("WARN", f"Hotkey '{text}' ignored: {e}", "YELLOW")
                continue
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                callbacks[i] = fn
            else:
                log("WARN", f"Hotkey '{text}' is already used by another app", "YELLOW")
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in callbacks:
                try:
                    callbacks[msg.wParam]()
                except Exception as e:
                    log("ERROR", f"Hotkey action failed: {e}", "RED")
        for i in callbacks:
            user32.UnregisterHotKey(None, i)
