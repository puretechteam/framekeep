# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import ctypes, os, threading, time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

def idle_seconds():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000

def screen_locked():
    """True while the lock screen (or another secure desktop such as UAC) has the input desktop."""
    desk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desk:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        if not user32.GetUserObjectInformationW(desk, UOI_NAME, name, ctypes.sizeof(name), None):
            return False
        return name.value.lower() != "default"
    finally:
        user32.CloseDesktop(desk)

def foreground_exe():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)
    return None

def pause_reason(cfg, locked, exe, idle):
    """Highest-priority reason to auto-pause right now, or None."""
    if cfg["pause_on_lock"] and locked:
        return "locked"
    if exe and exe in cfg["private_apps"]:
        return "private"
    if cfg["idle_pause_minutes"] and idle >= cfg["idle_pause_minutes"] * 60:
        return "idle"
    return None

class Watcher:
    """Polls lock state, the foreground app and input idle time, and tells the recorder when to auto-pause."""
    def __init__(self, recorder, get_cfg, interval=0.25):
        self.recorder, self.get_cfg, self.interval = recorder, get_cfg, interval
        self.reason = None
        self.running = True

    def start(self):
        threading.Thread(target=self._loop, name="watcher", daemon=True).start()

    def _loop(self):
        while self.running:
            try:
                reason = pause_reason(self.get_cfg(), screen_locked(), foreground_exe(), idle_seconds())
            except Exception:
                reason = None
            if reason != self.reason:
                self.reason = reason
                self.recorder.set_auto_pause(reason)
            time.sleep(self.interval)
