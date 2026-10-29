# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import ctypes, os, threading
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi32 = ctypes.windll.wtsapi32
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_DESKTOPSWITCH = 0x0003, 0x0020
WINEVENT_OUTOFCONTEXT = 0
WM_QUIT, WM_APP, WM_WTSSESSION_CHANGE = 0x0012, 0x8000, 0x02B1
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8
NOTIFY_FOR_THIS_SESSION = 0
HWND_MESSAGE = wintypes.HWND(-3)
QS_ALLINPUT, PM_REMOVE, INFINITE = 0x04FF, 0x0001, 0xFFFFFFFF

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                  wintypes.DWORD, wintypes.DWORD)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

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
    """Tells the recorder when to auto-pause: screen locked, a private app in front, or the user away.

    Event driven: Windows reports foreground-window changes, desktop switches and session lock/unlock to this
    thread, so it sleeps until something actually changes. While recording is wanted it also wakes when the idle
    limit could next be reached, plus a slow safety re-check. (v3.0 polled all three checks 4 times a second.)"""
    SAFETY = 5.0        # seconds between safety re-checks while recording is wanted
    AWAY_POLL = 0.5     # how quickly an idle pause notices the user is back

    def __init__(self, recorder, get_cfg):
        self.recorder, self.get_cfg = recorder, get_cfg
        self.reason = None
        self.running = True
        self.session_locked = False
        self._thread_id = None
        self._callbacks = []    # ctypes callbacks must outlive the hooks that call them

    def start(self):
        threading.Thread(target=self._loop, name="watcher", daemon=True).start()

    def wake(self):
        """Re-check now (recording started or stopped, settings changed). Safe from any thread."""
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0)

    def stop(self):
        self.running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _check(self):
        try:
            locked = self.session_locked or screen_locked()
            reason = pause_reason(self.get_cfg(), locked, foreground_exe(), idle_seconds())
        except Exception:
            reason = None
        if reason != self.reason:
            self.reason = reason
            self.recorder.set_auto_pause(reason)

    def _timeout_ms(self):
        if not self.recorder.wanted:
            return INFINITE     # nothing to pause; lock and foreground events still keep the reason current
        wait = self.SAFETY
        minutes = self.get_cfg()["idle_pause_minutes"]
        if minutes:
            left = minutes * 60 - idle_seconds()
            wait = min(wait, left) if left > 0 else self.AWAY_POLL
        return int(max(wait, 0.1) * 1000)

    def _on_session(self, hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE and wparam in (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK):
            self.session_locked = wparam == WTS_SESSION_LOCK
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _listen(self):
        """Hook foreground and desktop changes, and session lock/unlock (needs a hidden message-only window)."""
        on_event = WINEVENTPROC(lambda *_: None)    # the event wakes the loop, which then re-checks
        hooks = [user32.SetWinEventHook(e, e, None, on_event, 0, 0, WINEVENT_OUTOFCONTEXT)
                 for e in (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_DESKTOPSWITCH)]
        wndproc = WNDPROC(self._on_session)
        self._callbacks = [on_event, wndproc]
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=wndproc, hInstance=hinst, lpszClassName="FrameKeepWatcher")
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, "FrameKeepWatcher", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinst, None)
        if hwnd:
            wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)
        return hooks, hwnd

    def _loop(self):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # create this thread's message queue
        self._thread_id = kernel32.GetCurrentThreadId()
        hooks, hwnd = self._listen()
        try:
            while self.running:
                self._check()
                user32.MsgWaitForMultipleObjects(0, None, False, self._timeout_ms(), QS_ALLINPUT)
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_QUIT:
                        self.running = False
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for h in hooks:
                if h:
                    user32.UnhookWinEvent(h)
            if hwnd:
                wtsapi32.WTSUnRegisterSessionNotification(hwnd)
                user32.DestroyWindow(hwnd)
