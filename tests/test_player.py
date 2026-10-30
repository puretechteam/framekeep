import json, os, time
from types import SimpleNamespace
from framekeep import playback
from framekeep.app import App
from framekeep.config import DEFAULT_CONFIG, migrate, validate

def library(tmp_path):
    """A desktop folder and one camera folder with chunks on one day, plus a motion event."""
    day = "2026-10-30"
    desk = tmp_path / "rec"
    cam_dir = desk / "Cameras" / "Front door" / day
    (desk / day).mkdir(parents=True)
    cam_dir.mkdir(parents=True)
    for name in ("09-00-00.mp4", "09-10-00.mp4", "13-30-00-1.mp4"):
        (desk / day / name).write_bytes(b"x")
    for name in ("09-05-00.mp4", "09-06-00.mp4"):
        (cam_dir / name).write_bytes(b"x")
    t0 = time.mktime(time.strptime(f"{day} 09:05:30", "%Y-%m-%d %H:%M:%S"))
    (cam_dir / "events.jsonl").write_text(json.dumps({"start": t0, "end": t0 + 8, "peak": 0.1}) + "\n")
    cfg = validate(dict(DEFAULT_CONFIG) | {"log_root": str(desk), "cameras": [
        {"id": "cam1", "name": "Front door", "folder": "Front door", "kind": "device", "device": "x"}]})
    return cfg, day, desk, cam_dir, t0

def test_sources_days_and_files(tmp_path):
    cfg, day, desk, cam_dir, _ = library(tmp_path)
    assert [s[:2] for s in playback.sources(cfg)] == [("desktop", "Desktop"), ("cam1", "Front door")]
    assert playback.days(cfg) == [day]
    files = playback.day_files(str(desk), day)
    assert [os.path.basename(f) for f in files] == ["09-00-00.mp4", "09-10-00.mp4", "13-30-00-1.mp4"]  # oldest first
    assert playback.day_files("", day) == []

def test_locate_and_wall_time(tmp_path):
    cfg, day, desk, cam_dir, _ = library(tmp_path)
    assert playback.locate(cfg, str(cam_dir / "09-06-00.mp4")) == ("cam1", day)
    assert playback.locate(cfg, str(desk / day / "09-00-00.mp4")) == ("desktop", day)
    assert playback.locate(cfg, str(tmp_path / "elsewhere" / day / "09-00-00.mp4")) == (None, None)
    t = playback.wall_time(str(desk / day / "13-30-00-1.mp4"), 12)
    assert time.strftime("%H:%M:%S", time.localtime(t)) == "13:30:12"
    assert playback.wall_time(str(desk / day / "09-00-00.mp4"), -5) == playback.wall_time(str(desk / day / "09-00-00.mp4"))
    assert playback.wall_time(str(tmp_path / "clip.mp4")) is None

def test_motion_events_and_jumps(tmp_path):
    cfg, day, _, _, t0 = library(tmp_path)
    events = playback.motion_events(cfg, ["desktop", "cam1"], day)
    assert events == [(t0, t0 + 8, "Front door")]
    assert playback.next_event(events, t0 - 60, 1) == t0
    assert playback.next_event(events, t0 + 1, 1) is None
    assert playback.next_event(events, t0 + 60, -1) == t0
    assert playback.next_event(events, t0, -1) is None

def test_tiles_and_bar_math():
    assert playback.tiles(1) == [(0, 0, 1, 2)]
    assert playback.tiles(3)[0] == (0, 0, 1, 2) and len(playback.tiles(3)) == 3   # main spans the top row
    assert len(playback.tiles(9)) == playback.MAX_TILES
    assert playback.x_for(150, 100, 200, 400) == 200
    assert playback.t_for(200, 100, 200, 400) == 150
    assert playback.t_for(-50, 100, 200, 400) == 100 and playback.t_for(999, 100, 200, 400) == 200

def test_clip_name_is_safe():
    t = time.mktime(time.strptime("2026-10-30 14:02:08", "%Y-%m-%d %H:%M:%S"))
    assert playback.clip_name('Front: door?', t) == "Front door 2026-10-30 14-02-08.mp4"
    assert playback.clip_name("...", t, ".mkv") == "Clip 2026-10-30 14-02-08.mkv"

def test_player_setting_migrates_and_layouts_validate():
    assert migrate({"schema_version": 4, "player": "auto"})["player"] == "framekeep"
    assert migrate({"schema_version": 4, "player": "builtin"})["player"] == "framekeep"
    assert validate(dict(DEFAULT_CONFIG) | {"player": "builtin"})["player"] == "framekeep"   # unknown -> default
    cfg = validate(dict(DEFAULT_CONFIG) | {"player_layouts": [
        {"name": "Desk", "sources": ["desktop", "cam1", "cam1"], "audio": "cam1"},
        {"name": "Desk", "sources": ["cam2"]},                    # duplicate name dropped
        {"name": "Empty", "sources": []},                         # nothing to show dropped
        {"name": "Bad audio", "sources": ["cam1"], "audio": "cam9"},
        "junk"], "player_last": {"sources": ["a", "b", "c", "d", "e"], "audio": "e"}})
    assert cfg["player_layouts"] == [{"sources": ["desktop", "cam1"], "audio": "cam1", "name": "Desk"},
                                     {"sources": ["cam1"], "audio": None, "name": "Bad audio"}]
    assert cfg["player_last"] == {"sources": ["a", "b", "c", "d"], "audio": None}
    assert validate(dict(DEFAULT_CONFIG) | {"player_last": "junk"})["player_last"] == {}

def routing(cfg, gui=True):
    calls = []
    app = SimpleNamespace(cfg=cfg, gui=gui, open_player=lambda *a: calls.append(("player", a)))
    return app, calls

def test_open_recording_routes_to_player_or_default_app(tmp_path, monkeypatch):
    cfg, day, desk, cam_dir, _ = library(tmp_path)
    started = []
    monkeypatch.setattr(os, "startfile", lambda p: started.append(p), raising=False)
    app, calls = routing(cfg)
    App.open_recording(app, str(cam_dir / "09-05-00.mp4"), at=30)          # a motion event: 2 s before it
    (kind, (srcs, d, at)), = calls
    assert srcs == ["cam1"] and d == day and time.strftime("%H:%M:%S", time.localtime(at)) == "09:05:28"
    app, calls = routing(cfg | {"player": "system"})
    App.open_recording(app, str(desk / day / "09-00-00.mp4"))
    assert calls == [] and started == [str(desk / day / "09-00-00.mp4")]
    app, calls = routing(cfg)
    App.open_recording(app, str(tmp_path / "loose.mp4"))                   # not in a FrameKeep folder
    assert calls == [] and started[-1] == str(tmp_path / "loose.mp4")

def test_save_view_state_skips_recorder(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr("framekeep.app.save_config", lambda cfg: saved.append(cfg))
    app = SimpleNamespace(cfg=validate(dict(DEFAULT_CONFIG)))
    App.save_view_state(app, player_last={"sources": ["desktop"], "audio": "desktop"})
    assert app.cfg["player_last"] == {"sources": ["desktop"], "audio": "desktop"} and saved == [app.cfg]
