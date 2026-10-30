import io, json, os, shutil, subprocess, time
import pytest
from framekeep import cameras, cleanup, config, engine, motion
from framekeep import station as station_mod
from framekeep.config import DEFAULT_CONFIG, validate

def cfg(**kw):
    return validate(dict(DEFAULT_CONFIG) | kw)

DSHOW_LIST = '''[in#0 @ 01] "USB Webcam" (video)
[in#0 @ 01]   Alternative name "@device_pnp_\\\\?\\usb#vid_048e&pid_0880&mi_00#7&23884205&0&0000#{65e8}\\global"
[in#0 @ 01] "OBS Virtual Camera" (video)
[in#0 @ 01]   Alternative name "@device_sw_{860BB310}\\{A3FCE0F5}"
[in#0 @ 01] "Headset Microphone (AB17X USB Audio)" (audio)
[in#0 @ 01]   Alternative name "@device_cm_{33D9}\\wave_{03E0}"
[in#0 @ 01] "Microphone (USB Webcam)" (audio)
[in#0 @ 01]   Alternative name "@device_cm_{33D9}\\wave_{C45F}"
Error opening input file dummy.'''
DSHOW_OPTIONS = '''[in#0 @ 01]   vcodec=mjpeg  min s=1920x1080 fps=30 max s=1920x1080 fps=30
[in#0 @ 01]   vcodec=mjpeg  min s=1280x720 fps=30 max s=1280x720 fps=30
[in#0 @ 01]   vcodec=mjpeg  min s=640x480 fps=30 max s=640x480 fps=30
[in#0 @ 01]   pixel_format=yuyv422  min s=1280x720 fps=5 max s=1280x720 fps=5
[in#0 @ 01]   pixel_format=yuyv422  min s=640x480 fps=20 max s=640x480 fps=30'''

class Run:
    def __init__(self, stderr):
        self.stderr, self.stdout, self.returncode = stderr, "", 1

class TextFF:
    ffmpeg = "ffmpeg"
    def __init__(self, text):
        self.text = text
    def run(self, args, timeout=30):
        return Run(self.text)

def device_cam(**kw):
    return config.validate_camera({"name": "Desk", "kind": "device", "device": "@dev", "label": "USB Webcam"} | kw)

def url_cam(**kw):
    return config.validate_camera({"name": "Door", "kind": "url", "url": "rtsp://10.0.0.5:554/s1", "user": "admin",
                                   "secret": cameras.protect("p@ss:w/rd")} | kw)

# ---- discovery ------------------------------------------------------------------------------------------------
def test_list_devices_keeps_stable_ids():
    devs = cameras.list_devices(TextFF(DSHOW_LIST))
    assert [n for n, _ in devs["video"]] == ["USB Webcam", "OBS Virtual Camera"]
    assert devs["video"][0][1].startswith("@device_pnp_")
    assert devs["audio"][1] == ("Microphone (USB Webcam)", "@device_cm_{33D9}\\wave_{C45F}")

def test_mic_pairs_by_name():
    devs = cameras.list_devices(TextFF(DSHOW_LIST))
    assert cameras.pair_mic("USB Webcam", devs["audio"]).endswith("{C45F}")
    assert cameras.pair_mic("OBS Virtual Camera", devs["audio"]) is None

def test_mode_choice_prefers_the_smallest_fast_enough_mode():
    modes = cameras.device_modes(TextFF(DSHOW_OPTIONS), "@dev")
    assert ("mjpeg", 1280, 720, 30.0) in modes and ("yuyv422", 640, 480, 30.0) in modes
    assert cameras.pick_mode(modes, 720, 15) == ("mjpeg", 1280, 720, 30.0)     # raw 720p only reaches 5 FPS
    assert cameras.pick_mode(modes, 480, 15) == ("yuyv422", 640, 480, 30.0)    # raw needs no decoding
    assert cameras.pick_mode(modes, 2160, 15) == ("mjpeg", 1920, 1080, 30.0)   # tallest available
    assert cameras.pick_mode([], 720, 15) is None
    assert cameras.mode_args(("mjpeg", 1280, 720, 30.0)) == ["-vcodec", "mjpeg", "-video_size", "1280x720", "-framerate", "30"]

def test_input_args():
    args = cameras.input_args(device_cam(mic="@mic"), ("mjpeg", 1280, 720, 30.0), audio=True)
    assert args[:2] == ["-f", "dshow"] and args[-1] == "video=@dev:audio=@mic"
    assert cameras.input_args(device_cam(mic="@mic"), None, audio=False)[-1] == "video=@dev"
    rtsp = cameras.input_args(url_cam())
    assert rtsp[:2] == ["-rtsp_transport", "tcp"] and rtsp[-1] == "rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.5:554/s1"
    http = cameras.input_args(url_cam(url="http://10.0.0.6:8080/video", user=""))
    assert http[0] == "-rw_timeout" and http[-1] == "http://10.0.0.6:8080/video"

def test_passwords_are_encrypted_and_masked():
    secret = cameras.protect("p@ss:w/rd")
    assert secret and "p@ss" not in secret and cameras.unprotect(secret) == "p@ss:w/rd"
    assert cameras.unprotect("garbage!!") == "" and cameras.protect("") == ""
    url = cameras.with_login("rtsp://10.0.0.5/s1", "admin", "p@ss:w/rd")
    assert cameras.mask(url) == "rtsp://admin:***@10.0.0.5/s1"
    assert "p@ss" not in cameras.mask("failed: p@ss:w/rd here", ["p@ss:w/rd"])
    assert "p%40ss" not in cameras.mask("open rtsp://x/p%40ss%3Aw%2Frd", ["p@ss:w/rd"])

def test_stream_info():
    line = "  Stream #0:0: Video: h264 (High), yuv420p(progressive), 1920x1080, 25 fps, 25 tbr, 90k tbn"
    assert cameras.stream_info(line) == ("h264", 1920, 1080, 25.0)
    assert cameras.stream_info("nothing") is None

# ---- config ---------------------------------------------------------------------------------------------------
def test_camera_config_defaults_and_validation():
    c = cfg()
    assert c["cameras"] == [] and c["storage_warn_gb"] == 100 and c["camera_cleanup"] is False
    assert c["camera_keep_days"] == 7 and c["camera_keep_motion_days"] == 30 and c["hotkey_cameras"] == ""
    c = cfg(cameras=[{"name": "Front: door?", "fps": 99, "sensitivity": 0, "preset": "nope", "kind": "x",
                      "pause_apps": [" Zoom.exe", "zoom.exe"]}, {"name": "Front door"}, "junk"],
            camera_keep_days=40, camera_keep_motion_days=10)
    a, b, junk = c["cameras"]
    assert a["folder"] == "Front door" and b["folder"] == "Front door 2" and junk["folder"] == "Camera"
    assert a["fps"] == 30 and a["sensitivity"] == 1 and a["preset"] == "desktop" and a["kind"] == "device"
    assert a["pause_apps"] == ["zoom.exe"] and a["motion"] is True and a["audio"] is False
    assert len({x["id"] for x in c["cameras"]}) == 3
    assert c["camera_keep_motion_days"] == 40          # motion footage is never kept shorter
    dup = cfg(cameras=[{"id": "same", "name": "A"}, {"id": "same", "name": "B"}])["cameras"]
    assert dup[0]["id"] != dup[1]["id"]

def test_schema_4_migration_and_camera_root():
    assert config.migrate({"schema_version": 3})["schema_version"] == 4
    assert config.migrate({"crf_value": 30})["schema_version"] == 4
    assert config.camera_root({"log_root": "D:\\rec", "camera_root": ""}) == os.path.join("D:\\rec", "Cameras")
    assert config.camera_root({"log_root": "D:\\rec", "camera_root": "E:\\cams"}) == "E:\\cams"
    assert config.camera_root({"log_root": "", "camera_root": ""}) == ""

# ---- commands -------------------------------------------------------------------------------------------------
def test_camera_encoding_is_tuned_for_cameras():
    screen = engine.encoder_args("libsvtav1", "balanced", 15)
    cam = engine.encoder_args("libsvtav1", "balanced", 15, camera=True)
    assert "scm=1" in screen[-1] and "scm=0" in cam[-1]
    assert "hierarchical-levels=3" in cam[-1] and "hierarchical-levels=4" in screen[-1]
    assert cam[cam.index("-g") + 1] == str(15 * engine.CAMERA_GOP)
    assert "tune-content=screen" not in engine.encoder_args("libaom-av1", "balanced", 15, camera=True)
    assert "psy-rd=0" not in " ".join(engine.encoder_args("libx264", "balanced", 15, camera=True))
    assert "psy-rd=0" in " ".join(engine.encoder_args("libx264", "balanced", 15))

def test_camera_command_encoding():
    c = cfg(chunk_minutes=30)
    cam = device_cam(timestamp=True, quiet_saver=True)
    cmd = engine.build_camera_cmd("ffmpeg", cam, c, "libsvtav1", r"C:\cams\Desk\%Y-%m-%d\%H-%M-%S.mp4",
                                  ["-f", "dshow", "-i", "video=@dev"], audio="opus")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "fps=15" in graph and "hqdn3d" in graph and "drawtext" in graph and "mpdecimate" in graph
    assert f"fps={engine.ANALYSIS_FPS}" in graph and "rgb24" in graph
    assert cmd[cmd.index("-f", cmd.index("[rec]")):][:4] == ["-f", "segment", "-strftime", "1"]
    assert cmd[cmd.index("-segment_time") + 1] == "1800" and "-segment_atclocktime" in cmd
    assert "libopus" in cmd and "0:a:0?" in cmd
    assert cmd[-4:] == ["-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"][-4:] and cmd[-1] == "pipe:1"
    assert "-skip_frame" not in cmd

def test_camera_command_copy_and_containers():
    c = cfg()
    cmd = engine.build_camera_cmd("ffmpeg", url_cam(), c, None, r"C:\cams\Door\%Y-%m-%d\%H-%M-%S.mkv",
                                  ["-rtsp_transport", "tcp", "-i", "rtsp://x"])
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd.index("-skip_frame") < cmd.index("-i")
    assert "matroska" in cmd and "-an" in cmd and "hqdn3d" not in " ".join(cmd)
    assert engine.camera_copies(url_cam()) and not engine.camera_copies(url_cam(timestamp=True))
    assert not engine.camera_copies(url_cam(keep_stream=False)) and not engine.camera_copies(device_cam())
    hevc = engine.build_camera_cmd("ffmpeg", device_cam(), c, "libx265", r"C:\c\%H-%M-%S.mp4", ["-i", "x"])
    assert hevc[hevc.index("-tag:v") + 1] == "hvc1" and "drawtext" not in " ".join(hevc)

def _real_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        return None
    out = subprocess.run([path, "-hide_banner", "-muxers"], capture_output=True, text=True).stdout
    filters = subprocess.run([path, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return path if " segment " in out and "hqdn3d" in filters and "drawtext" in filters else None

@pytest.mark.skipif(not _real_ffmpeg(), reason="needs an FFmpeg with segment, hqdn3d and drawtext")
def test_camera_command_runs_in_ffmpeg(tmp_path):
    """The real command (filter escaping, timestamp, segment muxer, analysis pipe) on a 3 s test source."""
    day = tmp_path / time.strftime("%Y-%m-%d")
    day.mkdir()
    cam = device_cam(timestamp=True, fps=10)
    pattern = str(tmp_path / "%Y-%m-%d" / "%H-%M-%S.mkv")
    cmd = engine.build_camera_cmd(_real_ffmpeg(), cam, cfg(), "libx264", pattern,
                                  ["-f", "lavfi", "-i", "testsrc2=s=640x480:r=10:d=3"])
    p = subprocess.run(cmd, capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode()[-500:]
    frames = len(p.stdout) // (engine.ANALYSIS_W * engine.ANALYSIS_H * 3)
    assert 10 <= frames <= 14                       # 4 a second for 3 s
    files = list(day.iterdir())
    assert len(files) == 1 and files[0].stat().st_size > 1000

# ---- motion ---------------------------------------------------------------------------------------------------
W, H = engine.ANALYSIS_W, engine.ANALYSIS_H

def frame(box=None, noise=0, seed=0):
    """A flat gray frame, optionally with a bright box (x, y, w, h) and pseudo-random noise of +-noise."""
    px = bytearray(b"\x60" * (W * H * 3))
    state = seed * 2654435761 + 1
    if noise:
        for i in range(len(px)):
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            px[i] = 0x60 + state % (2 * noise + 1) - noise
    if box:
        x0, y0, bw, bh = box
        for y in range(y0, y0 + bh):
            px[(y * W + x0) * 3:(y * W + x0 + bw) * 3] = b"\xf0" * (bw * 3)
    return bytes(px)

def test_grid_samples_every_fourth_pixel():
    assert len(motion.grid(frame(), W, H)) == (W // motion.STEP) * (H // motion.STEP)

def test_noise_is_not_motion_but_a_moving_box_is():
    events = []
    d = motion.MotionDetector(W, H, 5, on_end=lambda s, e, p: events.append((s, e, p)))
    t = 1000.0
    for i in range(20):                              # sensor noise only
        d.feed(frame(noise=8, seed=i), t)
        t += 0.25
    assert not d.active and d.level < 0.01
    for i in range(8):                               # a person-sized box walks across
        d.feed(frame(box=(10 + i * 15, 30, 40, 60), noise=8, seed=100 + i), t)
        t += 0.25
    assert d.active and d.active[0] == pytest.approx(1000.0 + 20 * 0.25 + 0.25)
    for i in range(30):                              # still again: ends 5 s after the last motion
        d.feed(frame(noise=8, seed=200 + i), t)
        t += 0.25
    assert not d.active and len(events) == 1
    start, end, peak = events[0]
    assert end > start and 0.05 < peak < 0.5

def test_sensitivity_changes_the_threshold():
    small = (90, 50, 12, 12)                         # a small object: ~1% of the picture
    hits = {}
    for sens in (1, 10):
        d = motion.MotionDetector(W, H, sens)
        d.feed(frame(), 0)
        for i in range(3):
            d.feed(frame(box=(small[0] + i * 12, *small[1:])), 0.25 * (i + 1))
        hits[sens] = bool(d.active)
    assert hits == {1: False, 10: True}

def test_event_log_and_lookup(tmp_path):
    log = motion.EventLog(str(tmp_path))
    t = time.mktime(time.strptime("2026-10-29 12:00:30", "%Y-%m-%d %H:%M:%S"))
    log.add(t, t + 12, 0.25)
    day = tmp_path / "2026-10-29"
    with open(day / "events.jsonl", "a") as f:
        f.write('{"start": broken\n')
    log.add(t - 600, t - 590, 0.1)
    events = motion.read_events(str(day))
    assert [e["start"] for e in events] == [t - 600, t] and events[1]["peak"] == 0.25
    chunks = [str(day / "11-30-00.mkv"), str(day / "12-00-00.mkv"), str(day / "12-30-00.mkv")]
    assert motion.chunk_start(chunks[1]) == t - 30
    assert motion.locate(t, chunks) == (chunks[1], pytest.approx(30))
    assert motion.locate(t - 7200, chunks) == (None, 0.0)

# ---- station --------------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self):
        self.pid = 4242
    def poll(self):
        return None

class FakeEncoder:
    instances = []
    frames = True                 # deliver analysis frames on start
    def __init__(self, cmd, path, label, mux_cmd=None, audio=None, reader=None):
        self.cmd, self.path, self.label, self.reader = cmd, path, label, reader
        self.running, self.stopped, self.proc, self.text = False, False, None, "ffmpeg exited"
        FakeEncoder.instances.append(self)
    def start(self):
        self.running, self.proc = True, FakeProc()
        if FakeEncoder.frames:
            self.reader(io.BytesIO(frame() * 2))
    def alive(self):
        return self.running
    def stop(self, timeout=15):
        self.running, self.stopped = False, True
    def pids(self):
        return [4242]
    def error_summary(self):
        return self.text

class FakeFF:
    ffmpeg = "ffmpeg"
    def __init__(self, override=""):
        pass
    def resolve(self, preset):
        return preset, "libx264"
    def muxers(self):
        return {"mp4", "matroska", "webm"}
    def camera_audio_codecs(self):
        return {"opus", "aac"}
    def run(self, args, timeout=30):
        return Run("")

@pytest.fixture
def station(monkeypatch, tmp_path):
    FakeEncoder.instances, FakeEncoder.frames = [], True
    monkeypatch.setattr(station_mod, "Encoder", FakeEncoder)
    monkeypatch.setattr(station_mod, "FFmpeg", FakeFF)
    c = cfg(log_root=str(tmp_path), cameras=[device_cam(mic="@mic", audio=True), url_cam(hotkey=False)])
    return station_mod.Station(c)

def recorder(st, i=0):
    rec = station_mod.CameraRecorder(st.cfg["cameras"][i], st)
    st.recorders[rec.cam["id"]] = rec
    return rec

def test_camera_starts_records_and_stops(station, tmp_path):
    rec = recorder(station)
    rec._tick(time.time())
    enc = FakeEncoder.instances[-1]
    assert rec.state == "starting" and enc.cmd[-1] == "pipe:1" and "video=@dev:audio=@mic" in enc.cmd
    assert rec.audio == "aac"                       # beside H.264, like desktop audio
    assert os.path.isdir(tmp_path / "Cameras" / "Desk" / time.strftime("%Y-%m-%d"))
    rec._tick(time.time())
    assert rec.state == "recording" and rec.frame()[0] == frame()
    rec.set_paused(True)
    rec._tick(time.time())
    assert enc.stopped and rec.state == "paused" and rec.enc is None
    rec.set_paused(False)
    rec._tick(time.time())
    assert len(FakeEncoder.instances) == 2

def test_failures_back_off_and_are_classified(station):
    FakeEncoder.frames = False
    rec = recorder(station, 1)
    rec._tick(time.time())
    enc = FakeEncoder.instances[-1]
    enc.text, enc.running = "Error opening input files: Error number -138 occurred", False
    rec._tick(time.time())
    assert rec.state == "offline" and rec.error == "Not reachable"
    first = rec.retry_at - time.time()
    rec.retry_at = 0
    rec._tick(time.time())
    FakeEncoder.instances[-1].text = "method DESCRIBE failed: 401 Unauthorized p@ss:w/rd"
    FakeEncoder.instances[-1].running = False
    rec._tick(time.time())
    assert "login" in rec.error and rec.retry_at - time.time() > first
    assert "p@ss" not in rec.error

def test_webcam_in_use_is_reported_as_busy(station):
    FakeEncoder.frames = False
    rec = recorder(station)
    rec._tick(time.time())
    enc = FakeEncoder.instances[-1]
    enc.text, enc.running = "Could not run graph (sometimes caused by a device already in use by other application)", False
    rec._tick(time.time())
    assert rec.state == "busy" and rec.error == "In use by another app"

def test_one_camera_failing_leaves_the_other_alone(station):
    a, b = recorder(station, 0), recorder(station, 1)
    a._tick(time.time()); b._tick(time.time())
    enc_a, enc_b = FakeEncoder.instances
    a._tick(time.time()); b._tick(time.time())
    enc_b.running = False
    a._tick(time.time()); b._tick(time.time())
    assert a.state == "recording" and a.enc is enc_a and not enc_a.stopped
    assert b.enc is None

def test_app_pause_and_hotkey(station):
    a, b = recorder(station, 0), recorder(station, 1)
    a.set_app_paused("zoom.exe")
    a._tick(time.time())
    assert a.state == "app_paused" and a.enc is None
    a.set_app_paused(None)
    a._tick(time.time())
    assert a.enc is not None
    station.toggle_hotkey()                          # only the cameras that follow the hotkey
    assert a.user_paused and not b.user_paused
    station.toggle_hotkey()
    assert not a.user_paused
    station.pause_all(True)
    assert station.any_paused() and a.user_paused and b.user_paused

def test_apply_config_restarts_only_changed_cameras(station, monkeypatch):
    monkeypatch.setattr(station_mod.CameraRecorder, "start_thread", lambda self: None)
    station.apply_config(station.cfg)
    first = dict(station.recorders)
    cams = [dict(station.cfg["cameras"][0], sensitivity=9), station.cfg["cameras"][1]]
    first[cams[0]["id"]].user_paused = True
    station.apply_config(station.cfg | {"cameras": cams})
    assert station.recorders[cams[1]["id"]] is first[cams[1]["id"]]
    assert station.recorders[cams[0]["id"]] is not first[cams[0]["id"]]
    assert station.recorders[cams[0]["id"]].user_paused        # a pause survives a settings change
    station.apply_config(station.cfg | {"cameras": cams[1:]})
    assert list(station.recorders) == [cams[1]["id"]]

# ---- storage --------------------------------------------------------------------------------------------------
DAY = 86400

def make(path, age, now, size=100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    os.utime(path, (now - age, now - age))
    return path

def storage_cfg(tmp_path, **kw):
    return cfg(log_root=str(tmp_path / "rec"), camera_cleanup=True, cameras=[{"name": "Desk"}], **kw)

def test_cleanup_plan_respects_ages_and_motion(tmp_path):
    now = time.time()
    c = storage_cfg(tmp_path)
    cam = tmp_path / "rec" / "Cameras" / "Desk"
    old_day = time.strftime("%Y-%m-%d", time.localtime(now - 10 * DAY))
    midnight = time.mktime(time.strptime(old_day, "%Y-%m-%d"))
    quiet = make(str(cam / old_day / "08-00-00.mkv"), now - (midnight + 8.5 * 3600), now)    # 08:00-08:30
    moving = make(str(cam / old_day / "09-00-00.mkv"), now - (midnight + 9.5 * 3600), now)   # 09:00-09:30
    fresh = make(str(cam / time.strftime("%Y-%m-%d") / "07-00-00.mkv"), 3600 * 2, now)
    ancient_day = time.strftime("%Y-%m-%d", time.localtime(now - 40 * DAY))
    ancient = make(str(cam / ancient_day / "09-00-00.mkv"), 40 * DAY, now)
    other = make(str(cam / old_day / "my notes.mkv"), 10 * DAY, now)
    extra = make(str(cam / old_day / "08-30-00.mp4.part"), 10 * DAY, now)
    s = motion.chunk_start(moving)
    with open(cam / old_day / "events.jsonl", "w") as f:
        f.write(json.dumps({"start": s + 60, "end": s + 70, "peak": 0.2}) + "\n")
    planned = {p for p, _ in cleanup.plan(c, now)}
    assert planned == {quiet, ancient}
    assert fresh not in planned and moving not in planned and other not in planned and extra not in planned
    assert {p for p, _ in cleanup.plan(c, now, active=[quiet])} == {ancient}

def test_cleanup_never_touches_desktop_recordings(tmp_path, monkeypatch):
    now = time.time()
    # the camera root is set to the folder that holds the desktop recordings: cleanup must refuse outright
    rec = tmp_path / "rec"
    desktop = make(str(rec / "2020-01-01" / "08-00-00.mp4"), 2000 * DAY, now)
    c = cfg(log_root=str(rec), camera_root=str(tmp_path), camera_cleanup=True, cameras=[{"name": "rec"}])
    assert cleanup.camera_folders(c) == [] and cleanup.plan(c, now) == []
    c = cfg(log_root=str(rec), camera_root=str(rec), camera_cleanup=True, cameras=[{"name": "2020-01-01"}])
    assert cleanup.plan(c, now) == []
    removed = []
    monkeypatch.setattr(os, "remove", lambda p: removed.append(p))
    assert cleanup.run(c) == (0, 0) and removed == [] and os.path.exists(desktop)

def test_cleanup_only_runs_when_turned_on(tmp_path):
    now = time.time()
    c = storage_cfg(tmp_path)
    cam = tmp_path / "rec" / "Cameras" / "Desk"
    old = make(str(cam / time.strftime("%Y-%m-%d", time.localtime(now - 9 * DAY)) / "08-00-00.mkv"), 9 * DAY, now)
    assert cleanup.run(c | {"camera_cleanup": False}) == (0, 0) and os.path.exists(old)
    assert cleanup.run(c) == (1, 100) and not os.path.exists(old)
    # cameras that are no longer configured keep their footage
    gone = make(str(tmp_path / "rec" / "Cameras" / "Old cam" / "2020-01-01" / "08-00-00.mkv"), 2000 * DAY, now)
    assert cleanup.run(c) == (0, 0) and os.path.exists(gone)

def test_usage_counts_desktop_and_cameras_once(tmp_path):
    now = time.time()
    c = storage_cfg(tmp_path)
    make(str(tmp_path / "rec" / "2026-01-01" / "08-00-00.mp4"), DAY, now, 1000)
    make(str(tmp_path / "rec" / "Cameras" / "Desk" / "2026-01-01" / "08-00-00.mkv"), DAY, now, 500)
    assert cleanup.usage(c) == 1500
    assert cleanup.usage(c | {"camera_root": str(tmp_path / "rec")}) == 1000   # no camera folder there

def test_cameras_never_use_h266():
    c = cfg(preset="ultra")
    assert station_mod.camera_preset(device_cam(), c) == "smallest"
    assert station_mod.camera_preset(device_cam(preset="compatible"), c) == "compatible"
    assert config.validate_camera({"preset": "ultra"})["preset"] == "desktop"
