# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Camera sources: DirectShow devices (webcams, capture cards, phone webcam apps and their mics), listed through
FFmpeg, and network cameras by URL (RTSP, or HTTP MJPEG). A network camera's password is kept encrypted with
Windows DPAPI, so only this Windows account can read it, and it is masked everywhere FrameKeep shows or logs a URL."""

import base64, ctypes, re, subprocess, sys, urllib.parse

DEVICE_LINE = re.compile(r'"(.+)" \((video|audio|none)\)')
ALT_LINE = re.compile(r'Alternative name "(.+)"')
MODE_LINE = re.compile(r"(vcodec|pixel_format)=(\S+)\s+min s=(\d+)x(\d+) fps=([\d.]+)\s+max s=(\d+)x(\d+) fps=([\d.]+)")
STREAM_LINE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+) (?:fps|tbr)")
CREDENTIALS = re.compile(r"(://[^/:@\s]+:)[^@\s/]*@")
URL_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")

# ---- DirectShow devices ---------------------------------------------------------------------------------------
def list_devices(ff):
    """{"video": [(name, id)], "audio": [(name, id)]}. id is the device's stable "alternative name", which still
    matches after the camera moves to another USB port and tells two identical cameras apart."""
    out = {"video": [], "audio": []}
    if not ff.ffmpeg:
        return out
    try:
        text = ff.run(["-f", "dshow", "-list_devices", "true", "-i", "dummy"], timeout=20).stderr
    except OSError:
        return out
    last = None
    for line in text.splitlines():
        m = DEVICE_LINE.search(line)
        if m and m.group(2) in out:
            last = [m.group(1), m.group(1)]
            out[m.group(2)].append(last)
            continue
        m = ALT_LINE.search(line)
        if m and last:
            last[1] = m.group(1)
            last = None
    return {kind: [tuple(d) for d in devs] for kind, devs in out.items()}

def device_modes(ff, device):
    """The capture modes a camera offers: [(format, width, height, max_fps)], format "mjpeg" or a pixel format."""
    try:
        text = ff.run(["-f", "dshow", "-list_options", "true", "-i", f"video={device}"], timeout=20).stderr
    except OSError:
        return []
    modes = set()
    for m in MODE_LINE.finditer(text):
        kind, fmt = m.group(1), m.group(2)
        modes.add((fmt if kind == "pixel_format" else fmt, int(m.group(6)), int(m.group(7)), float(m.group(8))))
    return sorted(modes)

def pick_mode(modes, height, fps):
    """The camera mode to open: the smallest one at least `height` tall that reaches `fps` (or the tallest one that
    does, when none is that tall). Uncompressed beats MJPEG at the same size, since it needs no decoding."""
    fast = [m for m in modes if m[3] >= fps] or list(modes)
    if not fast:
        return None
    tall = [m for m in fast if m[2] >= height]
    pool = tall or fast
    best_h = min(m[2] for m in pool) if tall else max(m[2] for m in pool)
    same = [m for m in pool if m[2] == best_h]
    return sorted(same, key=lambda m: (m[0] == "mjpeg", -m[3]))[0]

def mode_args(mode):
    if not mode:
        return []
    fmt, w, h, max_fps = mode
    kind = "-vcodec" if fmt == "mjpeg" else "-pixel_format"
    return [kind, fmt, "-video_size", f"{w}x{h}", "-framerate", f"{max_fps:g}"]

def pair_mic(video_name, audio_devices):
    """The microphone that belongs to a camera ("USB Webcam" -> "Microphone (USB Webcam)"), as its id, or None."""
    name = video_name.lower()
    for label, dev_id in audio_devices:
        lower = label.lower()
        if f"({name})" in lower or lower.startswith(name) or name in lower:
            return dev_id
    return None

# ---- Network cameras ------------------------------------------------------------------------------------------
def is_url(text):
    return str(text).strip().lower().startswith(URL_SCHEMES)

def with_login(url, user, password):
    """url with the login put in (a login already in url wins)."""
    parts = urllib.parse.urlsplit(url)
    if not user or "@" in parts.netloc:
        return url
    login = urllib.parse.quote(user, safe="") + (":" + urllib.parse.quote(password, safe="") if password else "")
    return urllib.parse.urlunsplit(parts._replace(netloc=f"{login}@{parts.netloc}"))

def mask(text, secrets=()):
    """text with any URL password replaced by ***, and any of `secrets` too (for FFmpeg's error lines)."""
    text = CREDENTIALS.sub(r"\1***@", str(text))
    for s in secrets:
        if s:
            text = text.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return text

# ---- DPAPI ----------------------------------------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi(data, protect):
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_uint32, ctypes.POINTER(_Blob)]
    if protect:
        ok = fn(ctypes.byref(src), "FrameKeep camera", None, None, None, 0x1, ctypes.byref(dst))   # UI_FORBIDDEN
    else:
        ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)

def protect(password):
    """An encrypted, base64 form of password for config.json ("" stays "")."""
    if not password:
        return ""
    if sys.platform != "win32":
        return base64.b64encode(password.encode()).decode()
    return base64.b64encode(_dpapi(password.encode("utf-8"), True)).decode()

def unprotect(secret):
    if not secret:
        return ""
    try:
        raw = base64.b64decode(secret)
        return (raw if sys.platform != "win32" else _dpapi(raw, False)).decode("utf-8")
    except (OSError, ValueError):
        return ""    # another Windows account or a damaged value: the user re-enters it

# ---- Inputs ---------------------------------------------------------------------------------------------------
def input_args(cam, mode=None, audio=False):
    """FFmpeg input options for a camera (without the analysis-only -skip_frame)."""
    if cam["kind"] == "url":
        url = with_login(cam["url"], cam.get("user", ""), unprotect(cam.get("secret", "")))
        if url.lower().startswith(("rtsp://", "rtsps://")):
            # TCP: no lost packets (smeared frames) on Wi-Fi; the timeout makes a dead camera end FFmpeg so the
            # station can reconnect
            return ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url]
        return ["-rw_timeout", "10000000", "-i", url]
    spec = f"video={cam['device']}"
    if audio and cam.get("mic"):
        spec += f":audio={cam['mic']}"
    # a 128 MB real-time buffer rides out a moment's stall (a slow disk, a busy CPU) without dropping frames
    return ["-f", "dshow", "-rtbufsize", "128M", *mode_args(mode), "-i", spec]

def stream_info(text):
    """(codec, width, height, fps) of the first video stream in FFmpeg's log, or None."""
    m = STREAM_LINE.search(text)
    return (m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))) if m else None

def test(ff, cam, mode=None, timeout=20):
    """Open a camera once: (one analysis-size RGB frame or None, (codec, w, h, fps) or None, error or None).
    The station's own FFmpeg holds a webcam while it records, so pause a recording camera before testing it."""
    from .engine import ANALYSIS_H, ANALYSIS_W, POPEN_FLAGS
    if not ff.ffmpeg:
        return None, None, "FFmpeg was not found"
    cmd = [ff.ffmpeg, "-hide_banner", "-loglevel", "info", *input_args(cam, mode), "-frames:v", "1", "-an",
           "-vf", f"scale={ANALYSIS_W}:{ANALYSIS_H}:force_original_aspect_ratio=decrease,"
                  f"pad={ANALYSIS_W}:{ANALYSIS_H}:-1:-1,format=rgb24", "-f", "rawvideo", "pipe:1"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=POPEN_FLAGS, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, None, "No picture within 20 seconds"
    except OSError as e:
        return None, None, str(e)
    log_text = p.stderr.decode("utf-8", "replace")
    frame = p.stdout if len(p.stdout) >= ANALYSIS_W * ANALYSIS_H * 3 else None
    if frame:
        return frame[:ANALYSIS_W * ANALYSIS_H * 3], stream_info(log_text), None
    lines = [l for l in log_text.splitlines() if l.strip() and not l.startswith(" ")]
    secret = unprotect(cam.get("secret", ""))
    return None, None, mask(lines[-1] if lines else f"FFmpeg exited with code {p.returncode}", [secret])
