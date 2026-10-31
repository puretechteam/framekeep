import ctypes, importlib, inspect, pkgutil, subprocess, sys, textwrap, time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
import customtkinter as ctk
import pytest
import framekeep, framekeep.gui
from framekeep.config import DEFAULT_CONFIG

def gui_classes():
    for m in pkgutil.iter_modules(framekeep.gui.__path__):
        mod = importlib.import_module("framekeep.gui." + m.name)
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ == mod.__name__ and issubclass(cls, tk.Misc):
                yield cls

def test_gui_classes_keep_tkinter_internals():
    """A window class that reuses a private tkinter/customtkinter name (3.1.0's RecordingsPage._root) breaks every
    event on it."""
    clashes = []
    for cls in gui_classes():
        bases = [b for b in cls.__mro__[1:] if not b.__module__.startswith("framekeep")]
        for name in vars(cls):
            if name.startswith("_") and not name.startswith("__") and any(name in vars(b) for b in bases):
                clashes.append(f"{cls.__name__}.{name}")
    assert clashes == []

@pytest.fixture(scope="module")
def window():
    try:
        r = ctk.CTk()   # one root for the module: cached images belong to the first Tk
    except tk.TclError:
        pytest.skip("no display")
    yield r
    r.destroy()

@pytest.fixture
def root(window):
    window.geometry("900x600-4000-4000")    # shown (events only reach mapped windows), but off-screen
    window.update()
    yield window
    for w in window.winfo_children():
        w.destroy()

def test_recordings_page_survives_events(root, tmp_path):
    """3.1.0 crashed out of mainloop as soon as the Recordings page got an event (it shadowed tkinter's _root)."""
    from framekeep.gui.recordings import RecordingsPage
    cfg = {**DEFAULT_CONFIG, "log_root": str(tmp_path), "cameras": []}
    app = SimpleNamespace(cfg=cfg, station=SimpleNamespace(active_files=lambda: []), ui_queue=None)
    page = RecordingsPage(root, SimpleNamespace(app=app, status={}))
    page.pack(fill="both", expand=True)
    page.on_show()
    errors = []
    root.report_callback_exception = lambda *exc: errors.append(exc[1])
    root.after(100, lambda: root.geometry("1000x700-4000-4000"))
    root.after(400, root.quit)
    root.mainloop()     # callback errors only surface here, as they did in the exe
    assert errors == []

def test_recordings_cards_reflow_on_resize(root, tmp_path):
    """The card grid gains columns when the window gets wider (it never reflowed in 3.0 and 3.1.0)."""
    from framekeep.gui.recordings import RecordingsPage
    day = tmp_path / "2026-10-31"
    day.mkdir()
    files = []
    for i in range(8):
        f = day / f"0{i}-00-00.mkv"
        f.write_bytes(b"\0" * 1024)
        files.append(str(f))
    cfg = {**DEFAULT_CONFIG, "log_root": str(tmp_path), "cameras": []}
    app = SimpleNamespace(cfg=cfg, station=SimpleNamespace(active_files=lambda: []), ui_queue=None)
    page = RecordingsPage(root, SimpleNamespace(app=app, status={"files": files}))     # "recording now": no probing
    page.pack(fill="both", expand=True)
    root.update()
    page.on_show()
    narrow = page.columns
    root.after(100, lambda: root.geometry("1900x700-4000-4000"))
    root.after(500, root.quit)
    root.mainloop()
    assert len(page.cards) == 8 and page.columns > narrow

STILL_ACTIVE = 259

def alive(pid):
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))

@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_owned_child_ends_when_framekeep_crashes(tmp_path):
    """A recorder FrameKeep started must not keep running after FrameKeep dies (3.1.0 left them recording)."""
    script = textwrap.dedent("""
        import os, subprocess, sys
        sys.path.insert(0, sys.argv[1])
        from framekeep import winproc
        child = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        print(child.pid, flush=True)
        print(winproc.own(child), flush=True)
        os._exit(1)     # a crash: nothing gets cleaned up
    """)
    repo = str(Path(framekeep.__file__).parents[1])
    out = subprocess.run([sys.executable, "-c", script, repo], capture_output=True, text=True, timeout=30)
    pid, *owned = out.stdout.split()
    pid = int(pid)
    try:
        assert owned == ["True"], out.stderr
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not alive(pid)
    finally:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
