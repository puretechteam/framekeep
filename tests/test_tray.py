import datetime, os, time
from types import SimpleNamespace
from framekeep import tray as tray_mod
from framekeep.app import App as FrameKeepApp

class FakeIcon:
    def __init__(self):
        self.updates, self.notes = [], []
    def update(self, icon=None, tooltip=None):
        self.updates.append((icon, tooltip))
    def notify(self, title, text, actions=(), on_click=None):
        self.notes.append((title, text, [a[0] for a in actions], on_click))

def cam(name, state="recording", motion=False):
    return {"name": name, "state": state, "motion": motion}

def make_tray(cams, over=False):
    app = SimpleNamespace(station=SimpleNamespace(status=lambda: cams, recorders={}), storage={"over": over})
    t = tray_mod.Tray(app)
    t.icon = FakeIcon()
    return t

def test_icon_badges_change_pixels():
    plain = tray_mod.current_icon("recording")
    assert plain.getpixel((53, 11))[3] == 0                         # empty corner
    assert tray_mod.current_icon("recording", motion=True).getpixel((53, 11))[:3] == (0x60, 0xA5, 0xFA)
    assert tray_mod.current_icon("recording", problem=True).getpixel((44, 52))[:3] == (0xF9, 0x73, 0x16)

def test_tooltip_and_icon_follow_cameras():
    t = make_tray([cam("Front door", motion=True), cam("Desk", state="offline")])
    t.update({"state": "recording", "reason": ""})
    icon, tip = t.icon.updates[-1]
    assert tip == "FrameKeep: Recording  ·  1 of 2 cameras  ·  motion: Front door"
    assert icon.getpixel((53, 11))[:3] == (0x60, 0xA5, 0xFA) and icon.getpixel((44, 52))[:3] == (0xF9, 0x73, 0x16)
    t.update()                                                       # same look: tooltip only, no new icon
    assert t.icon.updates[-1][0] is None

def test_tooltip_auto_pause_reason_and_storage_badge():
    t = make_tray([cam("Desk")], over=True)
    t.update({"state": "auto_paused", "reason": "locked"})
    icon, tip = t.icon.updates[-1]
    assert tip == "FrameKeep: Auto-paused (locked)  ·  1 camera"
    assert icon.getpixel((44, 52))[:3] == (0xF9, 0x73, 0x16)

def test_play_motion_finds_the_chunk_after_rollover(tmp_path):
    day = datetime.date.today()
    folder = tmp_path / "Front door" / day.strftime("%Y-%m-%d")
    folder.mkdir(parents=True)
    for name in ("10-00-00.mp4", "10-01-00.mp4", "10-02-00.mp4"):
        (folder / name).write_bytes(b"x")
    t = time.mktime(day.timetuple()) + 10 * 3600 + 65                # 10:01:05, while 10:02 is being written
    opened = []
    app = SimpleNamespace(station=SimpleNamespace(recorders={"c1": SimpleNamespace(folder=lambda: str(tmp_path / "Front door"))}),
                          open_recording=lambda path, at=None: opened.append((os.path.basename(path), at)))
    FrameKeepApp.play_motion(app, "c1", t)
    assert opened == [("10-01-00.mp4", 5.0)]
    FrameKeepApp.play_motion(app, "missing", t)
    assert len(opened) == 1

def test_motion_alert_offers_play_and_pause():
    notes = FakeIcon()
    app = SimpleNamespace(tray=SimpleNamespace(notify=lambda text, title, actions, on_click: notes.notify(title, text, actions, on_click)))
    FrameKeepApp._motion_alert(app, {"id": "c1", "name": "Front door"}, time.time())
    title, text, actions, click = notes.notes[0]
    assert title == "Motion: Front door" and text.startswith("Started at ") and actions == ["Play", "Pause camera"]
    assert callable(click)
