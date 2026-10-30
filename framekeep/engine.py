# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import collections, datetime, json, os, shutil, subprocess, sys, threading
from . import __version__
from .log import log
from .paths import FFMPEG_DIR

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
POPEN_FLAGS = CREATE_NO_WINDOW if sys.platform == "win32" else 0

PRESET_INFO = {
    "compatible": ("Compatible", "H.264", "Plays everywhere. Largest files."),
    "smaller":    ("Smaller", "H.265 / HEVC", "Helps most with video-heavy screens. Plays on most modern devices."),
    "smallest":   ("Smallest", "AV1 screen-content", "Much smaller than H.264 on text and UI. Plays in modern players and browsers."),
    "ultra":      ("Ultra", "H.266 / VVC", "Newest codec, smallest files. Plays in the FrameKeep player; make a shareable copy for other apps."),
    "lowcpu":     ("Low CPU", "GPU encoder", "Uses your graphics card's encoder. Near-zero CPU, larger files."),
}

# Encoders tried in order for each preset, and where to fall back when none of them work
PRESET_CHAINS = {
    "compatible": ["libx264"],
    "smaller": ["libx265"],
    "smallest": ["libsvtav1", "libaom-av1"],
    "ultra": ["libvvenc"],
    "lowcpu": ["av1_nvenc", "av1_qsv", "av1_amf", "hevc_nvenc", "hevc_qsv", "hevc_amf", "h264_nvenc", "h264_qsv", "h264_amf"],
}
PRESET_FALLBACK = {"ultra": "smallest", "smallest": "smaller", "smaller": "compatible", "lowcpu": "compatible", "compatible": None}

# Quality value per level, in each encoder's own scale. "balanced" was matched to ~VMAF 97 on screen content.
QUALITY = {
    "libx264":    {"high": 19, "balanced": 23, "saver": 27, "max_saver": 31},
    "libx265":    {"high": 22, "balanced": 26, "saver": 30, "max_saver": 34},
    "libsvtav1":  {"high": 33, "balanced": 40, "saver": 46, "max_saver": 52},
    "libaom-av1": {"high": 26, "balanced": 32, "saver": 38, "max_saver": 44},
    "libvvenc":   {"high": 19, "balanced": 23, "saver": 27, "max_saver": 31},
    "hw":         {"high": 21, "balanced": 26, "saver": 31, "max_saver": 36},
}
# Relative bits needed for the same quality, used to size the bitrate cap per codec
EFFICIENCY = {"h264": 1.0, "hevc": 0.7, "av1": 0.5, "vvc": 0.4}
CAP_BITS_PER_PIXEL = 0.2

# Encoder thread caps. Each thread carries its own frame buffers, so default thread pools sized to the
# CPU (16+ threads) cost hundreds of MB of RAM for no benefit at 5 FPS. VVenC gets more for real-time headroom
# (3 threads instead of 3.0's 4: 9% less memory, same files, still several times faster than real time).
THREADS = 2
VVENC_THREADS = 3 if (os.cpu_count() or 1) >= 8 else 2
# Look-ahead depth, measured on a desktop sample (docs/perf): x264 rc-lookahead=10 (preset slow uses 50) and
# SVT-AV1 hierarchical-levels=4 (default 5, a 32-frame mini-GOP) each cut encoder memory by about a fifth and
# came out ~2-3% smaller at the same quality. x265's look-ahead stays: shortening it cost quality.
X264_LOOKAHEAD = 10
SVTAV1_LEVELS = 4
CAMERA_GOP = 10     # seconds between camera keyframes
# SVT-AV1 on camera video: 3 hierarchical levels came out 19% smaller than 4 at the same VMAF (63 vs 78 MB/hr on the
# docs/perf webcam sample) and used less memory (387 vs 467 MB peak offline, 311 vs 402 MB live); 2 levels was larger.
SVTAV1_CAMERA_LEVELS = 3

def codec_family(encoder):
    if "vvenc" in encoder: return "vvc"
    if "av1" in encoder: return "av1"
    if "265" in encoder or "hevc" in encoder: return "hevc"
    return "h264"

def target_size(w, h, factor):
    """Downscaled size, rounded down to even numbers as 4:2:0 encoders require."""
    return max(2, int(w * factor) // 2 * 2), max(2, int(h * factor) // 2 * 2)

def cap_kbps(w, h, fps, encoder):
    return int(w * h * fps * CAP_BITS_PER_PIXEL * EFFICIENCY[codec_family(encoder)] / 1000)

def encoder_args(encoder, quality, fps, cap=None, camera=False):
    """Codec arguments for one encoder at a quality level. cap is a max bitrate in kbps or None.
    camera: tuned for camera video (natural, noisy pictures) instead of screen content, with a keyframe every
    CAMERA_GOP seconds so a motion event plays from close to where it starts."""
    gop = str(max(1, int(fps)) * (CAMERA_GOP if camera else 60))  # screen: one keyframe per minute of real frames
    if encoder in QUALITY:
        q = str(QUALITY[encoder][quality])
    else:
        q = str(QUALITY["hw"][quality])
    rate_cap = ["-maxrate", f"{cap}k", "-bufsize", f"{cap * 2}k"] if cap else []
    t = str(THREADS)
    if encoder == "libx264":
        psy = "" if camera else "psy-rd=0:"   # psychovisual tuning helps natural video and smears text
        return ["-c:v", "libx264", "-preset", "slow", "-crf", q, "-g", gop, "-pix_fmt", "yuv420p", "-threads", t,
                "-x264-params", f"{psy}aq-mode=3:ref=5:subme=9:trellis=2:bframes=3:rc-lookahead={X264_LOOKAHEAD}", *rate_cap]
    if encoder == "libx265":
        psy = "" if camera else "psy-rd=0:psy-rdoq=0:"
        return ["-c:v", "libx265", "-preset", "medium", "-crf", q, "-g", gop, "-pix_fmt", "yuv420p10le",
                "-x265-params", f"log-level=error:{psy}aq-mode=3:pools={t}:frame-threads=1", *rate_cap]
    if encoder == "libsvtav1":
        levels = SVTAV1_CAMERA_LEVELS if camera else SVTAV1_LEVELS
        params = f"scm={0 if camera else 1}:lp={t}:hierarchical-levels={levels}" + (f":mbr={cap}" if cap else "")
        return ["-c:v", "libsvtav1", "-preset", "8", "-crf", q, "-g", gop, "-pix_fmt", "yuv420p10le", "-svtav1-params", params]
    if encoder == "libaom-av1":
        return ["-c:v", "libaom-av1", "-usage", "realtime", "-cpu-used", "8", "-row-mt", "1", "-threads", t, "-crf", q,
                "-b:v", f"{cap}k" if cap else "0", "-g", gop, "-pix_fmt", "yuv420p10le",
                *([] if camera else ["-aom-params", "tune-content=screen", "-enable-palette", "1", "-enable-intrabc", "1"])]
    if encoder == "libvvenc":
        params = ["-vvenc-params", f"MaxBitrate={cap * 1000}"] if cap else []  # VVenC "capped CQF"
        return ["-c:v", "libvvenc", "-preset", "faster", "-qp", q, "-period", str(CAMERA_GOP if camera else 60), "-pix_fmt", "yuv420p10le",
                "-threads", str(VVENC_THREADS), *params]
    pix = "p010le" if codec_family(encoder) != "h264" else "nv12"
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p6", "-rc", "vbr", "-cq", q, "-b:v", "0", "-g", gop, "-pix_fmt", pix, *rate_cap]
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-preset", "slow", "-global_quality", q, "-g", gop, "-pix_fmt", pix, *rate_cap]
    if encoder.endswith("_amf"):
        return ["-c:v", encoder, "-quality", "quality", "-rc", "cqp", "-qp_i", q, "-qp_p", q, "-g", gop, "-pix_fmt", pix]
    raise ValueError(f"Unknown encoder {encoder}")

def output_pix_fmt(args):
    return args[args.index("-pix_fmt") + 1]

CONTAINER_INFO = {   # key -> (label, description)
    "mp4": ("MP4", "Plays almost everywhere."),
    "mkv": ("MKV", "Works with every format. A favorite for archiving."),
    "webm": ("WebM", "Open web format. AV1 only; other formats are saved as MKV."),
}
# Codec families each container can hold reliably. Anything else falls back (see resolve_container).
CONTAINER_CODECS = {"mp4": {"h264", "hevc", "av1", "vvc"}, "mkv": {"h264", "hevc", "av1", "vvc"}, "webm": {"av1"}}

MUXER_NAMES = {"mp4": "mp4", "mkv": "matroska", "webm": "webm"}

def resolve_container(container, encoder, muxers=None):
    """The container actually used: the chosen one if it can hold this codec (and this ffmpeg build can write it),
    else MKV, else MP4. muxers is the set this ffmpeg has, or None to skip that check."""
    family = codec_family(encoder)
    for c in (container, "mkv", "mp4"):
        if family in CONTAINER_CODECS.get(c, ()) and (muxers is None or MUXER_NAMES[c] in muxers):
            return c
    return "mp4"

def muxer_args(container, encoder, offset_starts=False):
    """Output options for a recording that stays playable even if FrameKeep or the PC dies mid-file.
    offset_starts: streams may start at different times (video and audio), which MP4 records in the moov."""
    if container == "mp4":
        # fragmented MP4 cut every 5 s regardless of keyframes, so no conversion step is ever needed
        # (frag_keyframe would hold data until the next keyframe). hvc1 lets Windows/Apple players open H.265.
        # delay_moov writes the moov with the first fragment instead of before it, so it can say when each stream
        # starts; a file killed before its first fragment is unplayable either way.
        tag = ["-tag:v", "hvc1"] if codec_family(encoder) == "hevc" else []
        moov = "+delay_moov" if offset_starts else "+empty_moov"
        return [*tag, "-movflags", moov + "+default_base_moof", "-frag_duration", "5000000", "-flush_packets", "1"]
    # Matroska/WebM write a cluster every 5 s; a killed file just lacks its index and still plays
    return ["-f", "webm" if container == "webm" else "matroska", "-cluster_time_limit", "5000", "-flush_packets", "1"]

def video_filters(cfg, w, h, pix_fmt, hw_frames=False):
    """Filter chain from captured frames to encoder-ready frames.

    Downscale first, in one pass straight to 8-bit 4:2:0 (or gray), then compare frames. mpdecimate can't read
    BGRA, so running it first made FFmpeg convert every full-size frame to 4:4:4 just to compare it: about half the
    filter CPU and ~30 MB of RAM for nothing."""
    chain = ["hwdownload", "format=bgra"] if hw_frames else []
    chain += [f"scale={w}:{h}:flags=area", "format=gray" if cfg["grayscale"] else "format=yuv420p"]
    if cfg["drop_duplicates"]:
        # keep at least one frame every 10 s so chunk durations stay accurate on a static screen
        chain.append(f"mpdecimate=max={max(1, int(cfg['fps'])) * 10}")
    chain.append(f"format={pix_fmt}")
    return ",".join(chain)

# Desktop audio (framekeep/audio.py captures it). Opus is the smallest; AAC sits beside H.264 so those files play
# everywhere. Bitrates follow the video quality level. Opus runs VBR with DTX, which halved what silence costs
# (2.0 -> 1.0 kbps) on the docs/perf sample; 60 ms frames were 31% smaller but measurably less faithful, so the
# default 20 ms stays.
AUDIO_INFO = {   # key -> (label, description)
    "auto": ("Automatic", "AAC with Compatible (H.264) files so they play everywhere, Opus with every other format."),
    "opus": ("Opus", "Smallest files. Plays in modern players, browsers and FrameKeep's player."),
    "aac": ("AAC", "Plays everywhere. Larger than Opus at the same quality."),
}
AUDIO_ENCODERS = {"opus": "libopus", "aac": "aac"}
AUDIO_PIPE_INPUT = ["-f", "f32le", "-sample_rate", "48000", "-ch_layout", "stereo"]   # what framekeep/audio.py writes
AUDIO_KBPS = {"opus": {"high": 96, "balanced": 48, "saver": 32, "max_saver": 24},
              "aac":  {"high": 160, "balanced": 128, "saver": 96, "max_saver": 64}}

def audio_codec(setting, encoder, container):
    """Audio codec for a recording: the user's pick, or AAC beside H.264 and Opus otherwise. WebM only holds Opus."""
    codec = setting if setting in AUDIO_ENCODERS else ("aac" if codec_family(encoder) == "h264" else "opus")
    return "opus" if container == "webm" else codec

def audio_args(codec, quality):
    kbps = AUDIO_KBPS[codec][quality]
    if codec == "opus":
        return ["-c:a", "libopus", "-b:a", f"{kbps}k", "-vbr", "on", "-application", "audio", "-dtx", "1"]
    return ["-c:a", "aac", "-b:a", f"{kbps}k"]

def capture_region(monitor, crop_taskbar):
    """(x, y, w, h) in desktop pixels, and the same relative to the monitor."""
    x, y, w, h = monitor.work if crop_taskbar else monitor.rect
    w, h = w // 2 * 2, h // 2 * 2
    return (x, y, w, h), (x - monitor.rect[0], y - monitor.rect[1], w, h)

# Stamps ddagrab frames on the wall clock (its own timestamps count from its first frame: this adds the first
# frame's wall time), so a piped recording's video and its separately encoded audio share one clock. gdigrab
# already stamps packets with the wall clock.
WALL_CLOCK_PTS = r"setpts=PTS+if(eq(N\,0)\,st(0\,RTCTIME/1000000/TB)\,ld(0))"

def capture_args(cfg, monitor, backend, wall_clock=False):
    (x, y, w, h), (rx, ry, _, _) = capture_region(monitor, cfg["crop_taskbar"])
    mouse = "1" if cfg["draw_mouse"] else "0"
    if backend == "ddagrab":
        src = f"ddagrab=output_idx={monitor.dxgi_output}:framerate={cfg['fps']}:dup_frames=0:draw_mouse={mouse}"
        if cfg["crop_taskbar"]:
            src += f":video_size={w}x{h}:offset_x={rx}:offset_y={ry}"
        if wall_clock:
            src += "," + WALL_CLOCK_PTS
        return ["-f", "lavfi", "-i", src], (w, h)
    return ["-f", "gdigrab", "-framerate", str(cfg["fps"]), "-draw_mouse", mouse,
            "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-i", "desktop"], (w, h)

def file_metadata():
    now = datetime.datetime.now().astimezone()
    return ["-metadata", f"comment=Recorded with FrameKeep {__version__}",
            "-metadata", f"creation_time={now.isoformat(timespec='seconds')}"]

def build_record_cmd(ffmpeg, cfg, monitor, encoder, backend, out_path, piped_start=None):
    """The ffmpeg command for one recording file. The container follows out_path's extension.

    With piped_start (wall-clock seconds the audio track starts at) this is the video half of a recording with
    desktop audio: timestamps count from piped_start on the wall clock, and it writes MP4 fragments to stdout for
    build_mux_cmd's FFmpeg, which writes out_path."""
    container = os.path.splitext(out_path)[1].lstrip(".").lower()
    piped = piped_start is not None
    cap_in, (sw, sh) = capture_args(cfg, monitor, backend, wall_clock=piped)
    tw, th = target_size(sw, sh, cfg["downscale_factor"])
    cap = cap_kbps(tw, th, cfg["fps"], encoder) if cfg["bitrate_cap"] else None
    enc = encoder_args(encoder, cfg["quality"], cfg["fps"], cap)
    if piped:
        # video timestamps stay at millisecond precision: the encoder default (1/FPS) rounded them to the frame grid,
        # which is harmless alone but up to half a frame off from the audio. Files came out the same size.
        enc += ["-enc_time_base:v", "1:1000"]
        # fragmented MP4 keeps decode timestamps (Matroska drops them, which upset reordering codecs like H.266)
        # and, with delay_moov, when the video starts relative to the audio
        out = ["-f", "mp4", "-movflags", "+delay_moov+default_base_moof", "-frag_duration", "1000000",
               "-flush_packets", "1", "pipe:1"]
    else:
        out = [*file_metadata(), *muxer_args(container, encoder), out_path]
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-n", "-filter_threads", "1",
            *(["-copyts", "-itsoffset", f"{-piped_start:.6f}"] if piped else []), *cap_in,
            "-vf", video_filters(cfg, tw, th, output_pix_fmt(enc), hw_frames=backend == "ddagrab"),
            "-fps_mode", "passthrough", *enc, *out]

def build_audio_cmd(ffmpeg, codec, quality):
    """FFmpeg encoding framekeep/audio.py's raw PCM (stdin) to Matroska (stdout), passed on every 200 ms so the
    muxer never waits on audio held in a half-built cluster."""
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", *AUDIO_PIPE_INPUT, "-i", "pipe:0",
            *audio_args(codec, quality), "-f", "matroska", "-cluster_time_limit", "200", "-flush_packets", "1", "pipe:1"]

def build_mux_cmd(ffmpeg, audio_pipe, out_path, encoder):
    """FFmpeg joining the piped video (stdin) and the encoded audio (named pipe) into out_path, copying both.
    Both count from the audio's start, so -copyts keeps them in sync."""
    container = os.path.splitext(out_path)[1].lstrip(".").lower()
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-nostdin", "-n", "-copyts",
            "-i", "pipe:0", "-thread_queue_size", "64", "-i", audio_pipe, "-map", "0:v", "-map", "1:a", "-c", "copy",
            *file_metadata(), *muxer_args(container, encoder, offset_starts=True), out_path]

# Cameras (framekeep/station.py). Each camera is one FFmpeg that runs for hours: the segment muxer starts a new
# file at every chunk boundary of the clock without stopping the capture, so there is no gap between files. A second,
# tiny output (ANALYSIS_W x ANALYSIS_H RGB, ANALYSIS_FPS a second) feeds motion detection and the live tiles.
ANALYSIS_W, ANALYSIS_H, ANALYSIS_FPS = 192, 108, 4
# Light denoise before encoding: on the docs/perf webcam sample it made live AV1 files 6% smaller for the same CPU
# (sensor noise is what an encoder otherwise spends bits on). FFmpeg's default mpdecimate dropped no camera frames at
# all (noise makes every frame different), so the quiet saver uses camera-tuned thresholds and stays opt-in (-11%
# on the still sample).
CAMERA_DENOISE = "hqdn3d=2:1.5:3:3"
CAMERA_DECIMATE = "mpdecimate=hi=64*24:lo=64*10:frac=0.5"
TIMESTAMP = (r"drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='%{localtime\:%Y-%m-%d %T}':x=10:y=10"
             ":fontsize=h/28:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=6")

def camera_copies(cam):
    """True when a network camera's own stream is kept as-is (no re-encoding)."""
    return cam["kind"] == "url" and cam.get("keep_stream", True) and not cam.get("timestamp")

def build_camera_cmd(ffmpeg, cam, cfg, encoder, pattern, inputs, audio=None):
    """The ffmpeg command recording one camera into strftime-named chunk files (pattern, e.g.
    <camera folder>/%Y-%m-%d/%H-%M-%S.mkv), plus raw analysis frames on stdout.

    inputs: framekeep.cameras.input_args(). encoder: None when the stream is copied. audio: "opus" | "aac" | None."""
    container = os.path.splitext(pattern)[1].lstrip(".").lower()
    fps, height = cam.get("fps", 15), cam.get("height", 720)
    analysis = (f"scale={ANALYSIS_W}:{ANALYSIS_H}:force_original_aspect_ratio=decrease:flags=area,"
                f"pad={ANALYSIS_W}:{ANALYSIS_H}:-1:-1,format=rgb24")
    if encoder:
        enc = encoder_args(encoder, cfg["quality"], fps, None, camera=True)
        rec = [f"format={output_pix_fmt(enc)}"]
        if cam.get("timestamp"):
            rec.insert(0, TIMESTAMP)
        if cam.get("quiet_saver"):
            rec.insert(0, f"{CAMERA_DECIMATE}:max={max(1, int(fps))}")   # still at least one frame a second
        graph = (f"[0:v]fps={fps},scale=-2:min({height}\\,ih):flags=area,{CAMERA_DENOISE},format=yuv420p,split=2[r][a];"
                 f"[r]{','.join(rec)}[rec];[a]fps={ANALYSIS_FPS},{analysis}[an]")
        video = ["-filter_complex", graph, "-map", "[rec]", "-fps_mode", "passthrough", *enc]
        if codec_family(encoder) == "hevc" and container == "mp4":
            video += ["-tag:v", "hvc1"]
        pre = []
    else:
        # only the analysis output decodes, and only keyframes, so a copied camera costs next to no CPU
        video = ["-filter_complex", f"[0:v:0]{analysis}[an]", "-map", "0:v:0", "-c:v", "copy"]
        pre = ["-skip_frame", "nokey"]
    sound = ["-map", "0:a:0?", *audio_args(audio, cfg["quality"])] if audio else ["-an"]
    if container == "mp4":
        seg_opts = ["-segment_format", "mp4", "-segment_format_options",
                    "movflags=+empty_moov+default_base_moof:frag_duration=5000000"]
    else:
        seg_opts = ["-segment_format", "webm" if container == "webm" else "matroska",
                    "-segment_format_options", "cluster_time_limit=5000"]
    seconds = max(60, int(cfg["chunk_minutes"]) * 60)
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-filter_threads", "1", *pre, *inputs,
            *video, *sound, "-metadata", f"comment=Recorded with FrameKeep {__version__}", "-flush_packets", "1",
            "-f", "segment", "-strftime", "1", "-segment_time", str(seconds), "-segment_atclocktime", "1",
            "-reset_timestamps", "1", *seg_opts, pattern,
            "-map", "[an]", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]

class FFmpeg:
    """Locates the ffmpeg tools and caches what this build and this PC can do."""
    def __init__(self, override=""):
        folder = self._pick_folder(override)
        tool = lambda name: os.path.join(folder, f"{name}.exe") if folder and os.path.isfile(os.path.join(folder, f"{name}.exe")) else None
        self.ffmpeg, self.ffprobe = tool("ffmpeg"), tool("ffprobe")
        self._encoders = None
        self._audio_encoders = set()
        self._muxers = None
        self._demuxers = None
        self._works = {}
        self._lock = threading.Lock()

    @staticmethod
    def _pick_folder(override):
        """An explicit override wins. Otherwise prefer whichever of the bundled build and the one on PATH has H.266."""
        if override:
            return os.path.dirname(override) if override.lower().endswith(".exe") else override
        candidates = [d for d in (FFMPEG_DIR, os.path.dirname(shutil.which("ffmpeg") or "")) if d and os.path.isfile(os.path.join(d, "ffmpeg.exe"))]
        for d in candidates:
            try:
                out = subprocess.run([os.path.join(d, "ffmpeg.exe"), "-hide_banner", "-encoders"], capture_output=True, text=True,
                                     timeout=15, creationflags=POPEN_FLAGS, stdin=subprocess.DEVNULL).stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
            if "libvvenc" in out:
                return d
        return candidates[0] if candidates else None

    def run(self, args, timeout=30):
        return subprocess.run([self.ffmpeg, "-hide_banner", *args], capture_output=True, text=True, errors="replace",
                              timeout=timeout, creationflags=POPEN_FLAGS, stdin=subprocess.DEVNULL)

    def encoders(self):
        with self._lock:
            if self._encoders is None:
                self._encoders = set()
                if self.ffmpeg:
                    for line in self.run(["-encoders"]).stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0].startswith("V"):
                            self._encoders.add(parts[1])
                        elif len(parts) >= 2 and parts[0].startswith("A"):
                            self._audio_encoders.add(parts[1])
            return self._encoders

    def audio_codecs(self):
        """Desktop-audio codecs this build can record: it needs the encoder and the raw PCM input."""
        self.encoders()
        with self._lock:
            if self._demuxers is None:
                self._demuxers = set()
                if self.ffmpeg:
                    for line in self.run(["-demuxers"]).stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and "D" in parts[0]:
                            self._demuxers.update(parts[1].split(","))
        if "f32le" not in self._demuxers:
            return set()
        return {c for c, e in AUDIO_ENCODERS.items() if e in self._audio_encoders}

    def camera_audio_codecs(self):
        """Audio codecs this build can encode a camera's sound with (the camera input supplies the audio)."""
        self.encoders()
        return {c for c, e in AUDIO_ENCODERS.items() if e in self._audio_encoders}

    def muxers(self):
        """Output formats this ffmpeg build can write (cached)."""
        with self._lock:
            if self._muxers is None:
                self._muxers = set()
                if self.ffmpeg:
                    for line in self.run(["-muxers"]).stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and "E" in parts[0]:
                            self._muxers.update(parts[1].split(","))
            return self._muxers

    def works(self, encoder):
        """Encode two tiny frames to prove the encoder (and for GPU encoders, the hardware) actually works."""
        if encoder not in self.encoders():
            return False
        if encoder not in self._works:
            args = encoder_args(encoder, "balanced", 5)
            try:
                p = self.run(["-f", "lavfi", "-i", "testsrc2=s=320x240:r=5", "-frames:v", "2",
                              "-vf", f"format={output_pix_fmt(args)}", *args, "-f", "null", "-"], timeout=30)
                self._works[encoder] = p.returncode == 0
            except subprocess.TimeoutExpired:
                self._works[encoder] = False
        return self._works[encoder]

    def resolve(self, preset):
        """(preset actually used, encoder) following the fallback chain, or (None, None)."""
        while preset:
            for encoder in PRESET_CHAINS[preset]:
                if self.works(encoder):
                    return preset, encoder
            preset = PRESET_FALLBACK[preset]
        return None, None

    def available_presets(self):
        return {p: next((e for e in chain if self.works(e)), None) for p, chain in PRESET_CHAINS.items()}

    def probe(self, path):
        """Codec, duration and size of a finished recording (read only)."""
        if not self.ffprobe:
            return {}
        try:
            p = subprocess.run([self.ffprobe, "-v", "error", "-show_entries",
                                "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", path],
                               capture_output=True, text=True, timeout=20, creationflags=POPEN_FLAGS, stdin=subprocess.DEVNULL)
            data = json.loads(p.stdout or "{}")
            streams = data.get("streams") or []
            video = next((st for st in streams if st.get("codec_type") == "video"), {})
            audio = next((st for st in streams if st.get("codec_type") == "audio"), {})
            return {"codec": video.get("codec_name"), "width": video.get("width"), "height": video.get("height"),
                    "audio": audio.get("codec_name"), "duration": float(data.get("format", {}).get("duration") or 0)}
        except (subprocess.TimeoutExpired, ValueError, OSError):
            return {}

    def thumbnail(self, src, dst, width=320, at=2):
        """Write one frame of src to dst (dst lives in AppData, never next to the recording)."""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        p = self.run(["-y", "-ss", f"{at:g}", "-i", src, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", dst], timeout=30)
        if p.returncode != 0 or not os.path.exists(dst):
            self.run(["-y", "-i", src, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", dst], timeout=30)
        return os.path.exists(dst)

    def share_copy(self, src):
        """Create a NEW H.264 (+ AAC) copy next to src for apps that can't play newer codecs. src is only read."""
        base, _ = os.path.splitext(src)
        dst = base + "_share.mp4"
        n = 1
        while os.path.exists(dst):
            dst = f"{base}_share-{n}.mp4"
            n += 1
        args = ["-n", "-i", src, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst]   # -c:a only applies if src has sound
        p = self.run(args, timeout=3600)
        return dst if p.returncode == 0 else None

def pipe(size):
    """(read fd, write fd) of an anonymous pipe with a `size`-byte buffer."""
    if sys.platform != "win32":
        return os.pipe()
    import _winapi, msvcrt
    read, write = _winapi.CreatePipe(None, size)
    return msvcrt.open_osfhandle(read, os.O_RDONLY), msvcrt.open_osfhandle(write, 0)

class Encoder:
    """One running recording: an ffmpeg process writing the file, or with desktop audio, the video ffmpeg piped
    into a muxing ffmpeg (mux_cmd) that also reads `audio` (a running framekeep.audio.AudioTrack).
    reader: called on its own thread with ffmpeg's stdout (a camera's analysis frames)."""
    def __init__(self, cmd, path, label, mux_cmd=None, audio=None, reader=None):
        self.cmd, self.path, self.label = cmd, path, label
        self.mux_cmd, self.audio, self.reader = mux_cmd, audio, reader
        self.tail = collections.deque(maxlen=20)
        self.proc = self.mux = None
        self.started = None

    def start(self):
        flags = POPEN_FLAGS | (BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 0)
        if not self.mux_cmd:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
                                         stdout=subprocess.PIPE if self.reader else subprocess.DEVNULL)
            if self.reader:
                threading.Thread(target=self.reader, args=(self.proc.stdout,), daemon=True).start()
        else:
            # video -> muxer through a 1 MB pipe (Windows' default is 4 KB), so a moment's wait on the muxer's side
            # never stalls the encoder
            read, write = pipe(1 << 20)
            try:
                self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=write, stderr=subprocess.PIPE,
                                             creationflags=flags)
                try:
                    self.mux = subprocess.Popen(self.mux_cmd, stdin=read, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                creationflags=flags)
                except OSError:
                    self.proc.kill()
                    raise
            finally:
                os.close(read)              # the processes hold their own copies now
                os.close(write)
            threading.Thread(target=self._drain, args=(self.mux,), daemon=True).start()
        self.started = datetime.datetime.now()
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()

    def _drain(self, proc):
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self.tail.append(line)

    def pids(self):
        return [p.pid for p in (self.proc, self.mux, self.audio and self.audio.proc) if p]

    def alive(self):
        return all(p.poll() is None for p in (self.proc, self.mux) if p) and self.proc is not None

    def request_stop(self):
        """Ask ffmpeg to stop capturing and finish the file cleanly ('q'). Returns immediately; safe to call twice."""
        if self.audio:
            self.audio.finish()             # the audio ends where the video was asked to
        if self.proc and self.proc.poll() is None and self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass

    def stop(self, timeout=15):
        """request_stop(), then wait for the file to be finished, killing ffmpeg only if it doesn't exit in time."""
        if not self.proc:
            return
        self.request_stop()
        self._wait(self.proc, timeout)
        if self.audio:
            self.audio.stop()
        if self.mux:
            self._wait(self.mux, timeout)
        if self.audio:
            self.audio.close()

    def _wait(self, proc, timeout):
        if proc.poll() is None:
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                log("WARN", f"ffmpeg did not stop in time, ending it: {os.path.basename(self.path)}", "YELLOW")
                proc.kill()
                proc.wait()

    def error_summary(self):
        if self.tail:
            return self.tail[-1]
        codes = [p.returncode for p in (self.proc, self.mux) if p and p.returncode]
        return f"ffmpeg exited with code {codes[0] if codes else '?'}"
