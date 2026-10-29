import pytest
from framekeep import engine
from framekeep.config import DEFAULT_CONFIG, validate
from framekeep.monitors import Monitor

MON = Monitor(1, r"\\.\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), True, 0)
SIDE = Monitor(2, r"\\.\DISPLAY2", (1920, 0, 2560, 1440), (1920, 0, 2560, 1400), False, None)

def cfg(**kw):
    return validate(dict(DEFAULT_CONFIG) | kw)

def test_target_size_is_even():
    assert engine.target_size(1920, 1080, 0.5) == (960, 540)
    assert engine.target_size(2560, 1440, 0.333) == (852, 478)
    assert all(v % 2 == 0 for v in engine.target_size(1366, 768, 0.33))

@pytest.mark.parametrize("encoder", [e for chain in engine.PRESET_CHAINS.values() for e in chain])
@pytest.mark.parametrize("quality", ["high", "balanced", "saver", "max_saver"])
def test_every_encoder_builds_args(encoder, quality):
    args = engine.encoder_args(encoder, quality, 5, cap=300)
    assert args[:2] == ["-c:v", encoder]
    assert engine.output_pix_fmt(args)

def test_newer_codecs_use_10bit_and_screen_tools():
    assert engine.output_pix_fmt(engine.encoder_args("libvvenc", "balanced", 5)) == "yuv420p10le"
    av1 = engine.encoder_args("libsvtav1", "balanced", 5)
    assert "scm=1" in av1[av1.index("-svtav1-params") + 1]
    aom = engine.encoder_args("libaom-av1", "balanced", 5)
    assert "-enable-intrabc" in aom and "-enable-palette" in aom

def test_bitrate_cap_reaches_each_encoder():
    assert "-maxrate" in engine.encoder_args("libx264", "balanced", 5, cap=500)
    assert "mbr=500" in " ".join(engine.encoder_args("libsvtav1", "balanced", 5, cap=500))
    assert "MaxBitrate=500000" in " ".join(engine.encoder_args("libvvenc", "balanced", 5, cap=500))
    assert "-maxrate" not in engine.encoder_args("libx264", "balanced", 5, cap=None)

def test_cap_scales_with_codec_efficiency():
    assert engine.cap_kbps(960, 540, 5, "libvvenc") < engine.cap_kbps(960, 540, 5, "libsvtav1") < engine.cap_kbps(960, 540, 5, "libx264")

def test_record_cmd_never_overwrites_and_is_crash_safe():
    cmd = engine.build_record_cmd("ffmpeg", cfg(), MON, "libvvenc", "ddagrab", "out.mp4")
    assert "-n" in cmd and "-y" not in cmd
    flags = cmd[cmd.index("-movflags") + 1]
    assert "empty_moov" in flags and "frag_keyframe" not in flags
    assert "-frag_duration" in cmd
    assert cmd[-1] == "out.mp4"

def test_container_follows_extension():
    mkv = engine.build_record_cmd("ffmpeg", cfg(), MON, "libsvtav1", "ddagrab", "o.mkv")
    assert mkv[mkv.index("-f", mkv.index("-metadata")) + 1] == "matroska"
    assert "-cluster_time_limit" in mkv and "-movflags" not in mkv and "-n" in mkv
    webm = engine.build_record_cmd("ffmpeg", cfg(), MON, "libsvtav1", "ddagrab", "o.webm")
    assert webm[webm.index("-f", webm.index("-metadata")) + 1] == "webm"

def test_hvc1_tag_only_in_mp4():
    assert engine.build_record_cmd("ffmpeg", cfg(), MON, "libx265", "ddagrab", "o.mp4").count("hvc1") == 1
    assert "hvc1" not in engine.build_record_cmd("ffmpeg", cfg(), MON, "libx265", "ddagrab", "o.mkv")
    assert "hvc1" not in engine.build_record_cmd("ffmpeg", cfg(), MON, "libx264", "ddagrab", "o.mp4")

def test_resolve_container():
    assert engine.resolve_container("webm", "libsvtav1") == "webm"
    assert engine.resolve_container("webm", "av1_nvenc") == "webm"
    assert engine.resolve_container("webm", "libx264") == "mkv"   # WebM only holds AV1
    assert engine.resolve_container("webm", "libvvenc") in ("mkv", "mp4")
    for enc in ("libx264", "libx265", "libsvtav1"):
        assert engine.resolve_container("mkv", enc) == "mkv"
        assert engine.resolve_container("mp4", enc) == "mp4"
    # an ffmpeg build without the WebM muxer falls back to MKV rather than failing to record
    assert engine.resolve_container("webm", "libsvtav1", {"mp4", "matroska"}) == "mkv"
    assert engine.resolve_container("mkv", "libsvtav1", {"mp4"}) == "mp4"

def test_ddagrab_input_and_filters():
    cmd = engine.build_record_cmd("ffmpeg", cfg(fps=7), MON, "libx264", "ddagrab", "o.mp4")
    src = cmd[cmd.index("-i") + 1]
    assert src.startswith("ddagrab=output_idx=0:framerate=7:dup_frames=0")
    vf = cmd[cmd.index("-vf") + 1]
    assert vf == "hwdownload,format=bgra,scale=960:540:flags=area,format=yuv420p,mpdecimate=max=70,format=yuv420p"

def test_frames_are_compared_after_downscaling():
    """mpdecimate can't read BGRA, so in front of the scaler it makes FFmpeg convert every full-size frame first
    (3.0 did this: about half the filter CPU). It must come after the downscale to 8-bit 4:2:0 or gray."""
    for gray in (False, True):
        chain = engine.video_filters(cfg(grayscale=gray), 960, 540, "yuv420p10le", hw_frames=True).split(",")
        i = next(i for i, f in enumerate(chain) if f.startswith("mpdecimate"))
        assert chain[i - 2].startswith("scale=960:540") and chain[i - 1] == ("format=gray" if gray else "format=yuv420p")
        assert chain[-1] == "format=yuv420p10le"

def test_gdigrab_uses_desktop_offsets():
    cmd = engine.build_record_cmd("ffmpeg", cfg(), SIDE, "libx264", "gdigrab", "o.mp4")
    assert cmd[cmd.index("-offset_x") + 1] == "1920"
    assert cmd[cmd.index("-video_size") + 1] == "2560x1440"
    assert "hwdownload" not in cmd[cmd.index("-vf") + 1]

def test_crop_taskbar_uses_work_area():
    cmd = engine.build_record_cmd("ffmpeg", cfg(crop_taskbar=True), MON, "libx264", "ddagrab", "o.mp4")
    assert "video_size=1920x1032" in cmd[cmd.index("-i") + 1]
    assert "scale=960:516" in cmd[cmd.index("-vf") + 1]

def test_options_change_filters():
    vf = lambda **kw: engine.video_filters(cfg(**kw), 960, 540, "yuv420p")
    assert "mpdecimate" not in vf(drop_duplicates=False)
    assert "format=gray" in vf(grayscale=True)

def test_codec_family():
    assert [engine.codec_family(e) for e in ("libx264", "hevc_nvenc", "libsvtav1", "av1_qsv", "libvvenc")] == \
           ["h264", "hevc", "av1", "av1", "vvc"]

def test_encoder_stop_is_idempotent(tmp_path):
    """request_stop() then stop() (how the recorder closes every display at once) must not touch a closed pipe."""
    import sys
    fake_ffmpeg = [sys.executable, "-c", "import sys; sys.exit(0 if sys.stdin.read(1) == 'q' else 1)"]
    e = engine.Encoder(fake_ffmpeg, str(tmp_path / "x.mp4"), "test")
    e.start()
    e.request_stop()
    e.request_stop()
    e.stop(timeout=10)
    assert e.proc.returncode == 0

def test_resolve_falls_back(monkeypatch):
    ff = engine.FFmpeg.__new__(engine.FFmpeg)
    works = {"libx264", "libx265"}
    monkeypatch.setattr(ff, "works", lambda e: e in works, raising=False)
    assert engine.FFmpeg.resolve(ff, "ultra") == ("smaller", "libx265")
    assert engine.FFmpeg.resolve(ff, "lowcpu") == ("compatible", "libx264")
    works.clear()
    assert engine.FFmpeg.resolve(ff, "ultra") == (None, None)

def test_lookahead_settings_stay_lean():
    """The look-ahead depths that cut encoder memory ~20% in 3.1 (docs/perf) must not quietly revert."""
    assert "rc-lookahead=10" in engine.encoder_args("libx264", "balanced", 5)[-1]
    av1 = engine.encoder_args("libsvtav1", "balanced", 5)
    assert "hierarchical-levels=4" in av1[av1.index("-svtav1-params") + 1]
    assert engine.VVENC_THREADS <= 3
