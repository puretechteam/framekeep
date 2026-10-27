# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import ctypes, os, queue, subprocess, threading
from . import APP_NAME, autostart
from .config import DEFAULT_CONFIG, load_config, save_config, validate
from .hotkeys import Hotkeys
from .log import log
from .recorder import Recorder
from .tray import Tray
from .watchers import Watcher

kernel32 = ctypes.windll.kernel32
ERROR_ALREADY_EXISTS = 183
SHOW_EVENT = f"Local\\{APP_NAME}_show"

def claim_single_instance():
    """True if this is the only FrameKeep running. Otherwise asks the running one to show its window."""
    kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}_instance")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        event = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
        if event:
            kernel32.SetEvent(event)
            kernel32.CloseHandle(event)
        return False
    return True

class App:
    """Wires the recorder, watchers, hotkeys, tray and window together."""

    def __init__(self, gui=True, minimized=False):
        loaded = load_config()
        self.first_run = loaded is None or not loaded.get("onboarded")
        self.cfg = loaded or validate(dict(DEFAULT_CONFIG))
        self.gui, self.minimized = gui, minimized
        self.recorder = Recorder(self.cfg)
        self.watcher = Watcher(self.recorder, lambda: self.cfg)
        self.hotkeys = Hotkeys()
        self.tray = Tray(self)
        self.ui_queue = queue.Queue()   # work the Tk thread must do (the tray and hotkeys run on other threads)
        self.window = None
        self._closing = False
        self.recorder.subscribe(self._on_status)

    # ---- lifecycle ---------------------------------------------------------
    def run(self):
        self.recorder.start_thread()
        self.watcher.start()
        self._bind_hotkeys()
        self.tray.start()
        threading.Thread(target=self._wait_for_show_requests, daemon=True).start()
        if self.cfg["record_on_launch"] and self.cfg["onboarded"] and self.cfg["log_root"]:
            self.recorder.start()
        if self.gui:
            from .gui.app import MainWindow
            self.window = MainWindow(self)
            if self.minimized and not self.first_run:
                self.window.withdraw()
            self.window.mainloop()
        else:
            self._run_headless()

    def _run_headless(self):
        """v2-style: tray only, console logs. Picks a folder with a dialog on first run."""
        if not self.cfg["log_root"]:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            folder = filedialog.askdirectory(title="Select FrameKeepOS Folder")
            root.destroy()
            if folder:
                self.save_settings(self.cfg | {"log_root": folder, "onboarded": True})
        stop = threading.Event()
        self._headless_stop = stop
        while not stop.wait(0.2):
            self._drain_ui_queue()

    def quit(self):
        if self._closing:
            return
        self._closing = True
        log("STOPPED", "FrameKeepOS shutting down", "YELLOW")
        self.hotkeys.stop()
        self.watcher.running = False
        self.recorder.shutdown()
        self.tray.stop()
        if self.window:
            self.ui_queue.put(self.window.destroy)
        elif getattr(self, "_headless_stop", None):
            self._headless_stop.set()

    # ---- settings ----------------------------------------------------------
    def save_settings(self, new_cfg):
        new_cfg = validate(new_cfg)
        if new_cfg["start_with_windows"] != autostart.is_enabled():
            try:
                autostart.set_enabled(new_cfg["start_with_windows"])
            except OSError as e:
                log("ERROR", f"Could not change Start with Windows: {e}", "RED")
        changed = new_cfg != self.cfg
        self.cfg = new_cfg
        save_config(new_cfg)
        if changed:
            log("CONFIG", "Settings saved", "GREEN")
            self.recorder.apply_config(new_cfg)
            self._bind_hotkeys()

    def _bind_hotkeys(self):
        self.hotkeys.set({self.cfg["hotkey_toggle"]: self.recorder.toggle, self.cfg["hotkey_pause"]: self.recorder.toggle_pause})

    # ---- cross-thread plumbing ---------------------------------------------
    def _on_status(self, status):
        self.tray.update(status)

    def show_window(self):
        if self.window:
            self.ui_queue.put(self.window.show)

    def _wait_for_show_requests(self):
        event = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
        while not self._closing and event:
            if kernel32.WaitForSingleObject(event, 1000) == 0:
                self.show_window()

    def _drain_ui_queue(self):
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            fn()

    # ---- recordings --------------------------------------------------------
    def open_recording(self, path, codec=None):
        """Play a recording. H.265/H.266 go to the bundled ffplay unless the user prefers the system player."""
        ff = self.recorder.ff
        player = self.cfg["player"]
        builtin = player == "builtin" or (player == "auto" and codec in ("hevc", "vvc"))
        if builtin and ff.ffplay:
            subprocess.Popen([ff.ffplay, "-hide_banner", "-loglevel", "error", "-window_title", os.path.basename(path), path],
                             creationflags=0x08000000)
        else:
            os.startfile(path)

    @staticmethod
    def show_in_folder(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
