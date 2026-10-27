# FrameKeep - (C) 2025 Tanner Lucier
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
    "ultra":      ("Ultra", "H.266 / VVC", "Newest codec, smallest files. Opens in FrameKeep's built-in player; make a shareable copy for other apps."),
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
# CPU (16+ threads) cost hundreds of MB of RAM for no benefit at 5 FPS. VVenC gets more for real-time headroom.
THREADS = 2
VVENC_THREADS = 4 if (os.cpu_count() or 1) >= 8 else 2

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

def encoder_args(encoder, quality, fps, cap=None):
    """Codec arguments for one encoder at a quality level. cap is a max bitrate in kbps or None."""
    gop = str(max(1, int(fps)) * 60)  # one keyframe per minute of real frames
    if encoder in QUALITY:
        q = str(QUALITY[encoder][quality])
    else:
        q = str(QUALITY["hw"][quality])
    rate_cap = ["-maxrate", f"{cap}k", "-bufsize", f"{cap * 2}k"] if cap else []
    t = str(THREADS)
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "slow", "-crf", q, "-g", gop, "-pix_fmt", "yuv420p", "-threads", t,
                "-x264-params", "psy-rd=0:aq-mode=3:ref=5:subme=9:trellis=2:bframes=3", *rate_cap]
    if encoder == "libx265":
        return ["-c:v", "libx265", "-preset", "medium", "-crf", q, "-g", gop, "-pix_fmt", "yuv420p10le",
                "-x265-params", f"log-level=error:psy-rd=0:psy-rdoq=0:aq-mode=3:pools={t}:frame-threads=1", *rate_cap]
    if encoder == "libsvtav1":
        params = f"scm=1:lp={t}" + (f":mbr={cap}" if cap else "")
        return ["-c:v", "libsvtav1", "-preset", "8", "-crf", q, "-g", gop, "-pix_fmt", "yuv420p10le", "-svtav1-params", params]
    if encoder == "libaom-av1":
        return ["-c:v", "libaom-av1", "-usage", "realtime", "-cpu-used", "8", "-row-mt", "1", "-threads", t, "-crf", q,
                "-b:v", f"{cap}k" if cap else "0", "-g", gop, "-pix_fmt", "yuv420p10le",
                "-aom-params", "tune-content=screen", "-enable-palette", "1", "-enable-intrabc", "1"]
    if encoder == "libvvenc":
        params = ["-vvenc-params", f"MaxBitrate={cap * 1000}"] if cap else []  # VVenC "capped CQF"
        return ["-c:v", "libvvenc", "-preset", "faster", "-qp", q, "-period", "60", "-pix_fmt", "yuv420p10le",
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

def muxer_args(container, encoder):
    """Output options for a recording that stays playable even if FrameKeep or the PC dies mid-file."""
    if container == "mp4":
        # fragmented MP4 cut every 5 s regardless of keyframes, so no conversion step is ever needed
        # (frag_keyframe would hold data until the next keyframe). hvc1 lets Windows/Apple players open H.265.
        tag = ["-tag:v", "hvc1"] if codec_family(encoder) == "hevc" else []
        return [*tag, "-movflags", "+empty_moov+default_base_moof", "-frag_duration", "5000000", "-flush_packets", "1"]
    # Matroska/WebM write a cluster every 5 s; a killed file just lacks its index and still plays
    return ["-f", "webm" if container == "webm" else "matroska", "-cluster_time_limit", "5000", "-flush_packets", "1"]

def video_filters(cfg, w, h, pix_fmt, hw_frames=False):
    """Filter chain from captured frames to encoder-ready frames."""
    chain = ["hwdownload", "format=bgra"] if hw_frames else []
    if cfg["drop_duplicates"]:
        # keep at least one frame every 10 s so chunk durations stay accurate on a static screen
        chain.append(f"mpdecimate=max={max(1, int(cfg['fps'])) * 10}")
    chain.append(f"scale={w}:{h}:flags=area")
    if cfg["grayscale"]:
        chain.append("format=gray")
    chain.append(f"format={pix_fmt}")
    return ",".join(chain)

def capture_region(monitor, crop_taskbar):
    """(x, y, w, h) in desktop pixels, and the same relative to the monitor."""
    x, y, w, h = monitor.work if crop_taskbar else monitor.rect
    w, h = w // 2 * 2, h // 2 * 2
    return (x, y, w, h), (x - monitor.rect[0], y - monitor.rect[1], w, h)

def capture_args(cfg, monitor, backend):
    (x, y, w, h), (rx, ry, _, _) = capture_region(monitor, cfg["crop_taskbar"])
    mouse = "1" if cfg["draw_mouse"] else "0"
    if backend == "ddagrab":
        src = f"ddagrab=output_idx={monitor.dxgi_output}:framerate={cfg['fps']}:dup_frames=0:draw_mouse={mouse}"
        if cfg["crop_taskbar"]:
            src += f":video_size={w}x{h}:offset_x={rx}:offset_y={ry}"
        return ["-f", "lavfi", "-i", src], (w, h)
    return ["-f", "gdigrab", "-framerate", str(cfg["fps"]), "-draw_mouse", mouse,
            "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-i", "desktop"], (w, h)

def build_record_cmd(ffmpeg, cfg, monitor, encoder, backend, out_path):
    """The ffmpeg command for one recording file. The container follows out_path's extension."""
    container = os.path.splitext(out_path)[1].lstrip(".").lower()
    cap_in, (sw, sh) = capture_args(cfg, monitor, backend)
    tw, th = target_size(sw, sh, cfg["downscale_factor"])
    cap = cap_kbps(tw, th, cfg["fps"], encoder) if cfg["bitrate_cap"] else None
    enc = encoder_args(encoder, cfg["quality"], cfg["fps"], cap)
    now = datetime.datetime.now().astimezone()
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-n", "-filter_threads", "1", *cap_in,
            "-vf", video_filters(cfg, tw, th, output_pix_fmt(enc), hw_frames=backend == "ddagrab"),
            "-fps_mode", "passthrough", *enc,
            "-metadata", f"comment=Recorded with FrameKeep {__version__}",
            "-metadata", f"creation_time={now.isoformat(timespec='seconds')}",
            *muxer_args(container, encoder), out_path]

class FFmpeg:
    """Locates the ffmpeg tools and caches what this build and this PC can do."""
    def __init__(self, override=""):
        folder = self._pick_folder(override)
        tool = lambda name: os.path.join(folder, f"{name}.exe") if folder and os.path.isfile(os.path.join(folder, f"{name}.exe")) else None
        self.ffmpeg, self.ffprobe, self.ffplay = tool("ffmpeg"), tool("ffprobe"), tool("ffplay")
        self._encoders = None
        self._muxers = None
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
            return self._encoders

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
            p = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name,width,height:format=duration", "-of", "json", path],
                               capture_output=True, text=True, timeout=20, creationflags=POPEN_FLAGS, stdin=subprocess.DEVNULL)
            data = json.loads(p.stdout or "{}")
            stream = (data.get("streams") or [{}])[0]
            return {"codec": stream.get("codec_name"), "width": stream.get("width"), "height": stream.get("height"),
                    "duration": float(data.get("format", {}).get("duration") or 0)}
        except (subprocess.TimeoutExpired, ValueError, OSError):
            return {}

    def thumbnail(self, src, dst, width=320):
        """Write one frame of src to dst (dst lives in AppData, never next to the recording)."""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        p = self.run(["-y", "-ss", "2", "-i", src, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", dst], timeout=30)
        if p.returncode != 0 or not os.path.exists(dst):
            self.run(["-y", "-i", src, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", dst], timeout=30)
        return os.path.exists(dst)

    def share_copy(self, src):
        """Create a NEW H.264 copy next to src for apps that can't play newer codecs. src is only read."""
        base, _ = os.path.splitext(src)
        dst = base + "_share.mp4"
        n = 1
        while os.path.exists(dst):
            dst = f"{base}_share-{n}.mp4"
            n += 1
        args = ["-n", "-i", src, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", dst]
        p = self.run(args, timeout=3600)
        return dst if p.returncode == 0 else None

class Encoder:
    """One running ffmpeg recording process."""
    def __init__(self, cmd, path, label):
        self.cmd, self.path, self.label = cmd, path, label
        self.tail = collections.deque(maxlen=20)
        self.proc = None
        self.started = None

    def start(self):
        flags = POPEN_FLAGS | (BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 0)
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=flags)
        self.started = datetime.datetime.now()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self.tail.append(line)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def request_stop(self):
        """Ask ffmpeg to stop capturing and finish the file cleanly ('q'). Returns immediately; safe to call twice."""
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
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                log("WARN", f"ffmpeg did not stop in time, ending it: {os.path.basename(self.path)}", "YELLOW")
                self.proc.kill()
                self.proc.wait()

    def error_summary(self):
        return self.tail[-1] if self.tail else f"ffmpeg exited with code {self.proc.returncode if self.proc else '?'}"
