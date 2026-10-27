import os, shutil, time
import pytest
from framekeep import recorder as rec_mod
from framekeep.config import DEFAULT_CONFIG, validate
from framekeep.monitors import Monitor

MON = Monitor(1, r"\\.\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), True, 0)

class FakeEncoder:
    """Stands in for an ffmpeg process: 'records' by creating its output file once."""
    def __init__(self, cmd, path, label):
        self.cmd, self.path, self.label = cmd, path, label
        self.running = False
    def start(self):
        assert not os.path.exists(self.path), "recorder tried to reuse an existing file"
        with open(self.path, "xb") as f:   # 'x' fails if the file exists
            f.write(b"new recording")
        self.running = True
    def alive(self):
        return self.running
    def request_stop(self):
        self.running = False
    def stop(self, timeout=15):
        self.running = False
    def error_summary(self):
        return "boom"

class FakeFF:
    ffmpeg = "ffmpeg"
    def resolve(self, preset):
        return preset, "libx264"
    def muxers(self):
        return {"mp4", "matroska", "webm"}

@pytest.fixture
def recorder(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "Encoder", FakeEncoder)
    monkeypatch.setattr(rec_mod, "FFmpeg", lambda override="": FakeFF())
    monkeypatch.setattr(rec_mod, "list_monitors", lambda: [MON])
    r = rec_mod.Recorder(validate(dict(DEFAULT_CONFIG) | {"log_root": str(tmp_path), "chunk_minutes": 1}))
    r._prepare()
    return r

def test_chunk_name_never_returns_existing_path(tmp_path):
    first = rec_mod.chunk_name(str(tmp_path), "10-00-00")
    open(first, "w").close()
    second = rec_mod.chunk_name(str(tmp_path), "10-00-00")
    assert second != first and second.endswith("10-00-00-1.mp4")
    open(second, "w").close()
    assert rec_mod.chunk_name(str(tmp_path), "10-00-00").endswith("10-00-00-2.mp4")
    assert rec_mod.chunk_name(str(tmp_path), "10-00-00", ext="mkv").endswith("10-00-00.mkv")

def test_recorder_uses_configured_container(recorder):
    recorder._handle(("config", recorder.cfg | {"container": "mkv"}))
    recorder._handle(("start",))
    recorder._tick(time.time(), False)
    assert recorder.encoders[0].path.endswith(".mkv")
    assert recorder.status()["container"] == "mkv"
    recorder._handle(("stop",))

def test_start_pause_resume_stop_cycle(recorder):
    recorder._handle(("start",))
    recorder._tick(time.time(), False)
    assert recorder.status()["state"] == "recording"
    first = recorder.encoders[0].path
    recorder._handle(("pause",))
    assert recorder.status()["state"] == "paused" and not recorder.encoders
    recorder._handle(("resume",))
    time.sleep(1.1)  # new chunk gets a new timestamped name
    recorder._tick(time.time(), False)
    assert recorder.encoders[0].path != first
    recorder._handle(("stop",))
    assert recorder.status()["state"] == "idle"

def test_auto_pause_closes_and_resumes(recorder):
    recorder._handle(("start",))
    recorder._tick(time.time(), False)
    recorder._handle(("auto", "locked"))
    s = recorder.status()
    assert s["state"] == "auto_paused" and s["reason"] == "locked" and not recorder.encoders
    recorder._handle(("auto", None))
    recorder._tick(time.time(), False)
    assert recorder.status()["state"] == "recording"

def test_chunk_rolls_over_after_chunk_minutes(recorder):
    recorder._handle(("start",))
    recorder._tick(time.time(), False)
    first = recorder.encoders[0].path
    recorder.chunk_start -= 61
    time.sleep(1.1)
    recorder._tick(time.time(), False)
    assert recorder.encoders[0].path != first

def test_no_touch_guard(recorder, tmp_path, monkeypatch):
    """Existing recordings are byte-identical after a full session, and nothing under log_root is ever removed or renamed."""
    day = tmp_path / time.strftime("%Y-%m-%d")
    day.mkdir()
    old = day / "08-00-00.mp4"
    old.write_bytes(b"precious old recording")
    before = (old.read_bytes(), os.path.getmtime(old))
    root = str(tmp_path)

    def guard(name, real):
        def wrapped(path, *a, **kw):
            if os.path.abspath(str(path)).startswith(root):
                raise AssertionError(f"{name} called on {path}")
            return real(path, *a, **kw)
        return wrapped
    for mod, name in ((os, "remove"), (os, "unlink"), (os, "rename"), (os, "replace"), (shutil, "rmtree"), (shutil, "move")):
        monkeypatch.setattr(mod, name, guard(name, getattr(mod, name)))

    recorder._handle(("start",))
    for _ in range(3):
        recorder._tick(time.time(), False)
        recorder.chunk_start -= 61
        time.sleep(1.1)
    recorder._handle(("auto", "idle"))
    recorder._handle(("auto", None))
    recorder._tick(time.time(), False)
    recorder.encoders[0].running = False  # simulate an ffmpeg crash
    recorder._tick(time.time(), False)
    recorder._handle(("stop",))
    assert (old.read_bytes(), os.path.getmtime(old)) == before
    assert len(list(day.iterdir())) > 3

def test_early_ddagrab_failure_switches_to_gdi(recorder):
    recorder._handle(("start",))
    recorder._tick(time.time(), False)
    recorder.encoders[0].running = False
    recorder._tick(time.time(), False)
    assert recorder.backends[MON.device] == "gdigrab"
    assert recorder.retry_at > time.time()
