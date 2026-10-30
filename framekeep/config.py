# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import json, os, re, uuid
from .paths import CONFIG_FILE

SCHEMA_VERSION = 4
PRESETS = ("compatible", "smaller", "smallest", "ultra", "lowcpu")
QUALITIES = ("high", "balanced", "saver", "max_saver")
CAPTURE_BACKENDS = ("auto", "ddagrab", "gdigrab")
PLAYERS = ("framekeep", "system")
CONTAINERS = ("mp4", "mkv", "webm")
AUDIO_CODECS = ("auto", "opus", "aac")
CAMERA_KINDS = ("device", "url")
# "desktop": the same format as desktop recordings. No H.266 for cameras: VVenC recorded 34 s of every 60 on a 720p
# 15 FPS webcam sample (docs/perf), so a camera on Ultra would drop frames; "desktop" with Ultra uses AV1 instead.
CAMERA_PRESETS = ("desktop", "smallest", "smaller", "compatible", "lowcpu")
CAMERA_HEIGHTS = (360, 480, 720, 1080, 1440, 2160)

# One entry of cfg["cameras"]
CAMERA_DEFAULTS = {
    "id": "",
    "name": "Camera",
    "kind": "device",          # "device": DirectShow (webcam, capture card, phone webcam app); "url": network camera
    "device": "",              # DirectShow id (its stable alternative name)
    "label": "",               # the device's friendly name, for display
    "url": "",                 # rtsp:// or http(s):// without the password
    "user": "",
    "secret": "",              # password, DPAPI-encrypted (framekeep.cameras.protect)
    "folder": "",              # folder name under camera_root; fixed when the camera is added
    "enabled": True,
    "fps": 15,
    "height": 720,
    "preset": "desktop",
    "keep_stream": True,       # network cameras: copy the camera's own stream instead of re-encoding it
    "audio": False,
    "mic": "",                 # DirectShow id of the camera's microphone
    "motion": True,
    "sensitivity": 5,
    "quiet_saver": False,
    "timestamp": False,
    "notify": False,
    "hotkey": True,            # paused and resumed by the camera hotkey
    "pause_apps": [],          # paused while any of these programs runs (e.g. zoom.exe)
}
CAMERA_LIMITS = {"fps": (1, 30), "height": (240, 2160), "sensitivity": (1, 10)}

DEFAULT_CONFIG = {
    "schema_version": SCHEMA_VERSION,
    "log_root": "",
    "fps": 5,
    "downscale_factor": 0.5,
    "chunk_minutes": 60,
    "preset": "smallest",
    "quality": "balanced",
    "container": "mp4",
    "bitrate_cap": True,
    "monitors": ["primary"],
    "capture_backend": "auto",
    "draw_mouse": True,
    "drop_duplicates": True,
    "crop_taskbar": False,
    "grayscale": False,
    "audio": False,
    "audio_codec": "auto",
    "audio_exclude_apps": [],
    "idle_pause_minutes": 5,
    "pause_on_lock": True,
    "private_apps": ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe"],
    "hotkey_toggle": "ctrl+alt+f9",
    "hotkey_pause": "ctrl+alt+f10",
    "record_on_launch": False,
    "start_with_windows": False,
    "player": "framekeep",
    "player_layouts": [],             # saved player layouts: {name, sources, audio}
    "player_last": {},                # what the player showed last: {sources, audio}
    "cameras": [],
    "camera_root": "",                # "" = a Cameras folder inside log_root
    "hotkey_cameras": "",
    "storage_warn_gb": 100,           # 0 = no warning
    "camera_cleanup": False,          # delete old camera footage (never desktop recordings)
    "camera_keep_days": 7,
    "camera_keep_motion_days": 30,
    "ffmpeg_path": "",
    "onboarded": False,
}

# (min, max) for numeric keys; values outside are clamped on load
LIMITS = {"fps": (1, 30), "downscale_factor": (0.1, 1.0), "chunk_minutes": (1, 720), "idle_pause_minutes": (0, 240),
          "storage_warn_gb": (0, 100000), "camera_keep_days": (1, 3650), "camera_keep_motion_days": (1, 3650)}
CHOICES = {"preset": PRESETS, "quality": QUALITIES, "capture_backend": CAPTURE_BACKENDS, "player": PLAYERS,
           "container": CONTAINERS, "audio_codec": AUDIO_CODECS}

def crf_to_quality(crf):
    """Map a v2 x264 crf_value onto a v3 quality level."""
    if crf <= 20: return "high"
    if crf <= 25: return "balanced"
    if crf <= 30: return "saver"
    return "max_saver"

def migrate(cfg):
    """Upgrade an older config dict in place to the current schema. Touches the config only."""
    if cfg.get("schema_version", 2) < 3:
        crf = cfg.pop("crf_value", 23)
        use_crf = cfg.pop("use_crf", True)
        cfg.pop("codec", None)
        cfg.pop("convert_to_mp4", None)
        cfg["quality"] = crf_to_quality(crf) if use_crf else "high"
        cfg["schema_version"] = 3
    if cfg["schema_version"] < 4:      # 3.1 added cameras; validate() fills the new keys
        cfg["schema_version"] = SCHEMA_VERSION
    if cfg.get("player") in ("auto", "builtin"):   # 3.1 replaced ffplay with the FrameKeep player
        cfg["player"] = "framekeep"
    return cfg

def folder_name(name, taken=()):
    """A safe, unused folder name for a camera."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name)).strip(" .") or "Camera"
    base = base[:60]
    out, n = base, 2
    while out.lower() in {t.lower() for t in taken}:
        out, n = f"{base} {n}", n + 1
    return out

def apps(items):
    return sorted({str(a).strip().lower() for a in items if str(a).strip()})

MAX_LAYOUTS = 20

def validate_view(view):
    """A player layout or last view: {sources: [up to 4 ids], audio: id or None} (plus a name for layouts)."""
    if not isinstance(view, dict):
        return None
    srcs = [str(x) for x in view.get("sources", []) if isinstance(x, str)][:4] if isinstance(view.get("sources"), list) else []
    audio = view.get("audio") if view.get("audio") in srcs else None
    out = {"sources": list(dict.fromkeys(srcs)), "audio": audio}
    if "name" in view:
        out["name"] = str(view["name"]).strip()[:40]
    return out

def validate_camera(cam, taken=()):
    """One camera entry with defaults filled and values clamped. taken: folder names of the cameras before it."""
    out = CAMERA_DEFAULTS | {k: v for k, v in (cam if isinstance(cam, dict) else {}).items() if k in CAMERA_DEFAULTS}
    for key, (lo, hi) in CAMERA_LIMITS.items():
        try:
            out[key] = int(min(max(int(out[key]), lo), hi))
        except (TypeError, ValueError):
            out[key] = CAMERA_DEFAULTS[key]
    if out["kind"] not in CAMERA_KINDS:
        out["kind"] = "device"
    if out["preset"] not in CAMERA_PRESETS:
        out["preset"] = "desktop"
    for key, default in CAMERA_DEFAULTS.items():
        if isinstance(default, bool):
            out[key] = bool(out[key])
        elif isinstance(default, str):
            out[key] = str(out[key] or "").strip()
    out["pause_apps"] = apps(out["pause_apps"] if isinstance(out["pause_apps"], list) else [])
    out["id"] = out["id"] or uuid.uuid4().hex[:8]
    out["name"] = out["name"] or "Camera"
    if not out["folder"] or out["folder"].lower() in {t.lower() for t in taken}:
        out["folder"] = folder_name(out["name"], taken)
    return out

def validate(cfg):
    """Fill missing keys, coerce types and clamp values to sane ranges."""
    out = DEFAULT_CONFIG | {k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}
    for key, (lo, hi) in LIMITS.items():
        kind = type(DEFAULT_CONFIG[key])
        try:
            out[key] = kind(min(max(kind(out[key]), lo), hi))
        except (TypeError, ValueError):
            out[key] = DEFAULT_CONFIG[key]
    for key, allowed in CHOICES.items():
        if out[key] not in allowed:
            out[key] = DEFAULT_CONFIG[key]
    for key, default in DEFAULT_CONFIG.items():
        if isinstance(default, bool):
            out[key] = bool(out[key])
        elif isinstance(default, list) and not isinstance(out[key], list):
            out[key] = list(default)
    out["monitors"] = [str(m) for m in out["monitors"]] or ["primary"]
    for key in ("private_apps", "audio_exclude_apps"):
        out[key] = apps(out[key])
    cams, ids = [], set()
    for cam in out["cameras"]:
        cam = validate_camera(cam, [c["folder"] for c in cams])
        if cam["id"] in ids:
            cam["id"] = uuid.uuid4().hex[:8]
        ids.add(cam["id"])
        cams.append(cam)
    out["cameras"] = cams
    out["camera_keep_motion_days"] = max(out["camera_keep_motion_days"], out["camera_keep_days"])
    layouts, names = [], set()
    for view in out["player_layouts"]:
        view = validate_view(view)
        if view and view.get("name") and view["sources"] and view["name"] not in names:
            names.add(view["name"])
            layouts.append(view)
    out["player_layouts"] = layouts[:MAX_LAYOUTS]
    out["player_last"] = validate_view(out["player_last"]) or {}
    return out

def camera_root(cfg):
    """Where camera recordings go: the chosen folder, else a Cameras folder in the recordings folder."""
    if cfg.get("camera_root"):
        return cfg["camera_root"]
    return os.path.join(cfg["log_root"], "Cameras") if cfg.get("log_root") else ""

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return validate(migrate(cfg))
    return None

def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, CONFIG_FILE)
