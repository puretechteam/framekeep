# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import ctypes, gc, os, queue, subprocess, threading
from . import APP_NAME, autostart, stats, winproc
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
    """Wires the recorder, watchers, hotkeys, tray and window together.

    The window only exists while it's open. Closing it to the tray destroys it and releases its memory, and
    starting minimized never loads the interface at all; the main thread then just waits for work."""

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
        self._told_about_tray = False
        self.recorder.subscribe(self._on_status)

    # ---- lifecycle ---------------------------------------------------------
    def run(self):
        self.recorder.start_thread()
        self.watcher.start()
        self._bind_hotkeys()
        self.tray.start()
        threading.Thread(target=self._wait_for_show_requests, daemon=True).start()
        threading.Thread(target=stats.prune_thumbs, daemon=True).start()
        if self.cfg["record_on_launch"] and self.cfg["onboarded"] and self.cfg["log_root"]:
            self.recorder.start()
        if self.gui:
            if self.first_run or not self.minimized:
                self.ui_queue.put(self._open_window)
            self._run_gui()
        else:
            self._run_headless()

    def _run_gui(self):
        """Main thread: Tk's event loop while the window is open, otherwise a blocking wait for work."""
        while not self._closing:
            if self.window is None:
                try:
                    fn = self.ui_queue.get(timeout=5)
                except queue.Empty:
                    continue
                self._call(fn)
            else:
                self.window.mainloop()  # returns once the window is destroyed
                self.window = None
                self._release_ui()

    def _open_window(self):
        if self._closing:
            return
        if self.window is not None:
            self.window.show()
            return
        from .gui.app import MainWindow
        self.window = MainWindow(self)

    def close_window(self):
        """Close to the tray: destroy the window (Tk thread only). FrameKeep keeps running."""
        if self.window is not None:
            self.window.destroy()
        if not self._told_about_tray:
            self._told_about_tray = True
            self.tray.notify("FrameKeep is still running in the tray. Right-click the icon to quit.")

    def _release_ui(self):
        from .gui import theme
        theme.icon.cache_clear()   # its images belong to the destroyed window
        theme.release_trackers()
        gc.collect()
        winproc.trim_working_set()

    @staticmethod
    def _call(fn):
        try:
            fn()
        except Exception as e:
            if type(e).__name__ != "TclError":  # TclError: work queued for a window that has since closed
                log("ERROR", f"Interface error: {e!r}", "RED")

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
        while not self._closing:
            try:
                self._call(self.ui_queue.get(timeout=5))
            except queue.Empty:
                pass

    def quit(self):
        if self._closing:
            return
        self._closing = True
        log("STOPPED", "FrameKeepOS shutting down", "YELLOW")
        self.hotkeys.stop()
        self.watcher.stop()
        self.recorder.shutdown()
        self.tray.stop()
        window = self.window
        self.ui_queue.put(window.destroy if window else (lambda: None))  # wakes the main thread so it can exit

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
        self.watcher.wake()   # recording started/stopped or settings changed

    def show_window(self):
        if self.gui:
            self.ui_queue.put(self._open_window)

    def _wait_for_show_requests(self):
        event = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
        while not self._closing and event:
            if kernel32.WaitForSingleObject(event, 1000) == 0:
                self.show_window()

    def _drain_ui_queue(self):
        """Run queued work on the Tk thread. True if there was any."""
        ran = False
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                return ran
            ran = True
            self._call(fn)

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
