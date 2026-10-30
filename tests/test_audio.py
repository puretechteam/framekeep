import os, time
import pytest
from framekeep import audio, benchmark, config, engine
from framekeep import recorder as rec_mod
from framekeep.config import DEFAULT_CONFIG, validate
from framekeep.monitors import Monitor

MON = Monitor(1, r"\\.\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), True, 0)
SECOND = 10_000_000     # 100 ns units

def cfg(**kw):
    return validate(dict(DEFAULT_CONFIG) | kw)

# ---- settings -------------------------------------------------------------------------------------------------
def test_audio_is_off_by_default_and_validated():
    c = cfg()
    assert c["audio"] is False and c["audio_codec"] == "auto" and c["audio_exclude_apps"] == []
    c = cfg(audio=1, audio_codec="mp3", audio_exclude_apps=[" Discord.exe ", "discord.exe", ""])
    assert c["audio"] is True and c["audio_codec"] == "auto" and c["audio_exclude_apps"] == ["discord.exe"]
    assert config.validate({"audio_exclude_apps": "teams.exe"})["audio_exclude_apps"] == []

# ---- codec choice ---------------------------------------------------------------------------------------------
def test_auto_codec_is_aac_beside_h264_and_opus_otherwise():
    assert engine.audio_codec("auto", "libx264", "mp4") == "aac"
    assert engine.audio_codec("auto", "h264_nvenc", "mkv") == "aac"
    for enc in ("libx265", "libsvtav1", "libvvenc", "av1_nvenc"):
        assert engine.audio_codec("auto", enc, "mp4") == "opus"

def test_codec_override_and_webm():
    assert engine.audio_codec("aac", "libsvtav1", "mp4") == "aac"
    assert engine.audio_codec("opus", "libx264", "mp4") == "opus"
    assert engine.audio_codec("aac", "libsvtav1", "webm") == "opus"     # WebM only holds Opus

@pytest.mark.parametrize("codec", ["opus", "aac"])
def test_bitrate_follows_quality_level(codec):
    rates = [int(engine.audio_args(codec, q)[engine.audio_args(codec, q).index("-b:a") + 1][:-1])
             for q in ("high", "balanced", "saver", "max_saver")]
    assert rates == sorted(rates, reverse=True) and len(set(rates)) == 4

def test_opus_uses_vbr_and_dtx():
    args = engine.audio_args("opus", "balanced")
    assert args[:2] == ["-c:a", "libopus"] and "-dtx" in args and args[args.index("-vbr") + 1] == "on"
    assert engine.audio_args("aac", "balanced")[:2] == ["-c:a", "aac"]

# ---- commands -------------------------------------------------------------------------------------------------
def test_video_only_command_is_unchanged_by_audio_support():
    cmd = engine.build_record_cmd("ffmpeg", cfg(), MON, "libsvtav1", "ddagrab", "C:/rec/a.mp4")
    assert "-copyts" not in cmd and "-itsoffset" not in cmd and cmd[-1] == "C:/rec/a.mp4"
    assert "setpts" not in cmd[cmd.index("-i") + 1]
    assert "+empty_moov+default_base_moof" in cmd

def test_piped_video_counts_from_the_audio_start():
    start = 1_790_000_000.25
    cmd = engine.build_record_cmd("ffmpeg", cfg(), MON, "libsvtav1", "ddagrab", "C:/rec/a.mp4", piped_start=start)
    assert cmd[cmd.index("-itsoffset") + 1] == f"{-start:.6f}" and "-copyts" in cmd
    assert "RTCTIME" in cmd[cmd.index("-i") + 1]               # ddagrab stamped on the wall clock
    assert cmd[-1] == "pipe:1" and cmd[cmd.index("-f", cmd.index("-c:v")) + 1] == "mp4"
    assert "+delay_moov+default_base_moof" in cmd and "C:/rec/a.mp4" not in cmd
    gdi = engine.build_record_cmd("ffmpeg", cfg(), MON, "libx264", "gdigrab", "C:/rec/a.mp4", piped_start=start)
    assert "RTCTIME" not in " ".join(gdi)                        # gdigrab packets already carry wall-clock time

def test_audio_and_mux_commands():
    a = engine.build_audio_cmd("ffmpeg", "opus", "saver")
    assert a[a.index("-i") + 1] == "pipe:0" and a[-1] == "pipe:1" and "32k" in a
    assert a[a.index("-f") + 1] == "f32le"
    m = engine.build_mux_cmd("ffmpeg", r"\\.\pipe\x", "C:/rec/a.mp4", "libx265")
    assert m[m.index("-i") + 1] == "pipe:0" and r"\\.\pipe\x" in m and "-nostdin" in m and "-copyts" in m
    assert m[m.index("-c") + 1] == "copy" and ["-map", "0:v", "-map", "1:a"] == m[m.index("-map"):m.index("-map") + 4]
    assert "+delay_moov+default_base_moof" in m and ["-tag:v", "hvc1"] == m[m.index("-tag:v"):m.index("-tag:v") + 2]
    assert m[-1] == "C:/rec/a.mp4" and "-n" in m
    mkv = engine.build_mux_cmd("ffmpeg", "p", "C:/rec/a.mkv", "libsvtav1")
    assert mkv[mkv.index("-f") + 1] == "matroska"

# ---- capture timeline -----------------------------------------------------------------------------------------
def test_timeline_pads_gaps_and_skips_overlaps():
    tl = audio.Timeline(t0=100 * SECOND)
    assert tl.place(480, 100 * SECOND) == (0, 0)
    tl.written = 480
    assert tl.place(480, 100 * SECOND + SECOND // 100) == (0, 0)     # on time
    assert tl.place(480, 101 * SECOND) == (48000 - 480, 0)          # 1 s gap: silence first
    tl.written = 48000
    assert tl.place(480, 100 * SECOND) == (0, 480)                  # already covered: skip it
    assert tl.place(480, None) == (0, 0)                            # no timestamp: just append

def test_timeline_fill_holds_back():
    tl = audio.Timeline(t0=0)
    assert tl.fill(int(audio.HOLD_BACK * SECOND) // 2) == 0
    assert tl.fill(2 * SECOND) == int((2 - audio.HOLD_BACK) * audio.RATE)
    tl.written = 2 * audio.RATE
    assert tl.fill(2 * SECOND) == 0

def test_capture_stops_exactly_where_finished():
    out = []
    cap = audio.LoopbackCapture(out.append, t0=audio.qpc_100ns() - 2 * SECOND)
    cap._silence(audio.RATE)                          # 1 s written
    cap._end = cap.timeline.written + 100             # finish() a moment later
    cap._write(bytes(480 * audio.FRAME_BYTES), 480)   # only 100 frames of this still fit
    cap._silence(audio.RATE)                          # nothing more
    assert cap.timeline.written == audio.RATE + 100
    assert sum(map(len, out)) == (audio.RATE + 100) * audio.FRAME_BYTES

def test_excluded_app_roots():
    procs = [(1, 0, "explorer.exe"), (10, 1, "discord.exe"), (11, 10, "discord.exe"), (12, 10, "discord.exe"),
             (20, 1, "teams.exe"), (30, 1, "chrome.exe")]
    assert audio.excluded_roots(procs, {"discord.exe"}) == [10]
    assert sorted(audio.excluded_roots(procs, {"discord.exe", "teams.exe"})) == [10, 20]
    assert audio.excluded_roots(procs, set()) == []

def test_process_list_includes_this_process():
    assert any(pid == os.getpid() for pid, _, _ in audio.processes())

# ---- recorder -------------------------------------------------------------------------------------------------
class FakeEncoder:
    def __init__(self, cmd, path, label, mux_cmd=None, audio=None):
        self.cmd, self.path, self.label, self.mux_cmd, self.audio = cmd, path, label, mux_cmd, audio
        self.running = False
    def start(self):
        open(self.path, "xb").close()
        self.running = True
    def alive(self):
        return self.running
    def request_stop(self):
        self.running = False
    def stop(self, timeout=15):
        self.running = False
        if self.audio:
            self.audio.stop()
    def pids(self):
        return []
    def error_summary(self):
        return "boom"

class FakeTrack:
    started = []
    def __init__(self, cmd, exclude=()):
        self.cmd, self.exclude, self.path, self.start_time = cmd, exclude, r"\\.\pipe\fake", 1_790_000_000.0
        self.stopped = False
    def start(self):
        FakeTrack.started.append(self)
    def stop(self):
        self.stopped = True
    def close(self):
        pass
    def status(self):
        return {"device": "Speakers", "mode": "device", "peak": 0.5}

class FakeFF:
    ffmpeg = "ffmpeg"
    def __init__(self, codecs=("opus", "aac")):
        self.codecs = set(codecs)
    def resolve(self, preset):
        return preset, {"compatible": "libx264"}.get(preset, "libsvtav1")
    def muxers(self):
        return {"mp4", "matroska", "webm"}
    def audio_codecs(self):
        return self.codecs

@pytest.fixture
def make_recorder(tmp_path, monkeypatch):
    def make(codecs=("opus", "aac"), **kw):
        monkeypatch.setattr(rec_mod, "Encoder", FakeEncoder)
        monkeypatch.setattr(rec_mod, "FFmpeg", lambda override="": FakeFF(codecs))
        monkeypatch.setattr(rec_mod, "list_monitors", lambda: [MON])
        monkeypatch.setattr(audio, "AudioTrack", FakeTrack)
        r = rec_mod.Recorder(cfg(log_root=str(tmp_path), **kw))
        r._prepare()
        return r
    FakeTrack.started = []
    return make

def test_recording_with_audio_pipes_video_into_a_muxer(make_recorder):
    r = make_recorder(audio=True, audio_exclude_apps=["discord.exe"])
    assert r.audio_codec == "opus"
    r._handle(("start",))
    r._tick(time.time(), False)
    enc = r.encoders[0]
    assert enc.audio is FakeTrack.started[0] and FakeTrack.started[0].exclude == ["discord.exe"]
    assert enc.cmd[-1] == "pipe:1" and enc.mux_cmd[-1] == enc.path
    assert enc.cmd[enc.cmd.index("-itsoffset") + 1] == f"{-1_790_000_000.0:.6f}"
    assert r.status()["audio"] == {"codec": "opus", "kbps": 48, "device": "Speakers", "mode": "device", "peak": 0.5}
    r._handle(("stop",))
    assert FakeTrack.started[0].stopped

def test_compatible_preset_records_aac(make_recorder):
    r = make_recorder(audio=True, preset="compatible")
    assert r.audio_codec == "aac"

def test_audio_off_records_exactly_as_before(make_recorder):
    r = make_recorder()
    r._handle(("start",))
    r._tick(time.time(), False)
    enc = r.encoders[0]
    assert r.audio_codec is None and enc.mux_cmd is None and enc.cmd[-1] == enc.path and not FakeTrack.started
    assert r.status()["audio"] is None

def test_ffmpeg_without_audio_encoders_records_video_only(make_recorder):
    r = make_recorder(codecs=(), audio=True)
    assert r.audio_codec is None
    r._handle(("start",))
    r._tick(time.time(), False)
    assert r.encoders[0].mux_cmd is None and not FakeTrack.started

def test_audio_failure_never_stops_video(make_recorder, monkeypatch):
    def broken(self):
        raise OSError("no audio for you")
    monkeypatch.setattr(FakeTrack, "start", broken)
    r = make_recorder(audio=True)
    r._handle(("start",))
    r._tick(time.time(), False)
    enc = r.encoders[0]
    assert enc.mux_cmd is None and enc.cmd[-1] == enc.path and os.path.exists(enc.path)

def test_audio_goes_in_the_first_display_only(make_recorder, monkeypatch):
    second = Monitor(2, r"\\.\DISPLAY2", (1920, 0, 1920, 1080), (1920, 0, 1920, 1040), False, 1)
    r = make_recorder(audio=True, monitors=["all"])
    monkeypatch.setattr(rec_mod, "list_monitors", lambda: [MON, second])
    r._prepare()
    r._handle(("start",))
    r._tick(time.time(), False)
    assert [e.audio is not None for e in r.encoders] == [True, False]

# ---- benchmark ------------------------------------------------------------------------------------------------
def test_benchmark_adds_audio_cost():
    r = {"kbps": 400.0, "mb_per_hour": 180.0, "cpu_percent": 1.0, "ram_mb": 300, "realtime": 20, "vmaf": 96}
    a = {"codec": "opus", "kbps": 50.0, "cpu_percent": 0.03, "ram_mb": 20}
    out = benchmark.with_audio(r, a)
    assert out["kbps"] == 450.0 and out["mb_per_hour"] == 202.5 and out["ram_mb"] == 320 and out["cpu_percent"] == 1.0
    report = {"results": {"smallest": out}, "recommended": "smallest", "audio": {"opus": a}}
    assert "include desktop audio (Opus 50 kbps" in benchmark.explain(report)

def test_benchmark_audio_clip_is_fixed(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "BENCHMARK_DIR", str(tmp_path))
    path, seconds = benchmark.audio_sample(10)
    first = open(path, "rb").read()
    assert seconds == 12 and len(first) == seconds * 48000 * 8
    assert open(benchmark.audio_sample(10)[0], "rb").read() == first

def test_early_failure_with_audio_retries_video_only(make_recorder):
    r = make_recorder(audio=True)
    r._handle(("start",))
    now = time.time()
    r._tick(now, False)
    r.encoders[0].running = False                   # the audio/mux side died right away
    r._tick(now + 1, False)
    assert r.audio_codec is None and r.backends[MON.device] == "ddagrab"   # audio dropped first, capture untouched
    r._tick(r.retry_at, False)
    enc = r.encoders[0]
    assert enc.mux_cmd is None and enc.cmd[-1] == enc.path

def test_video_to_muxer_pipe_has_a_large_buffer():
    """The muxer may pause reading video for a moment; a 4 KB pipe would stall the encoder (and its capture)."""
    import threading
    read, write = engine.pipe(1 << 20)
    done = threading.Event()
    def fill():
        os.write(write, b"x" * 512 * 1024)     # no reader yet: only fits if the buffer is big
        done.set()
    threading.Thread(target=fill, daemon=True).start()
    try:
        assert done.wait(5)
        assert len(os.read(read, 1 << 20)) > 0
    finally:
        os.close(read)
        os.close(write)

def test_track_closes_encoder_input_when_capture_ends():
    """Ending the audio first lets the muxer finish the video (waiting on the video first deadlocked them)."""
    class Stdin:
        closed = False
        def close(self):
            self.closed = True
    class Proc:
        stdin = Stdin()
    track = audio.AudioTrack(["ffmpeg"])
    track.proc = Proc()
    cap = audio.LoopbackCapture(lambda b: None, audio.qpc_100ns(), on_done=track._end_input)
    cap.on_done()
    assert Proc.stdin.closed

def test_audio_encoder_hands_on_audio_promptly():
    a = engine.build_audio_cmd("ffmpeg", "aac", "balanced")
    assert a[a.index("-cluster_time_limit") + 1] == "200"
