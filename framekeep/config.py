# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import json, os
from .paths import CONFIG_FILE

SCHEMA_VERSION = 3
PRESETS = ("compatible", "smaller", "smallest", "ultra", "lowcpu")
QUALITIES = ("high", "balanced", "saver", "max_saver")
CAPTURE_BACKENDS = ("auto", "ddagrab", "gdigrab")
PLAYERS = ("auto", "builtin", "system")
CONTAINERS = ("mp4", "mkv", "webm")

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
    "idle_pause_minutes": 5,
    "pause_on_lock": True,
    "private_apps": ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe"],
    "hotkey_toggle": "ctrl+alt+f9",
    "hotkey_pause": "ctrl+alt+f10",
    "record_on_launch": False,
    "start_with_windows": False,
    "player": "auto",
    "ffmpeg_path": "",
    "onboarded": False,
}

# (min, max) for numeric keys; values outside are clamped on load
LIMITS = {"fps": (1, 30), "downscale_factor": (0.1, 1.0), "chunk_minutes": (1, 720), "idle_pause_minutes": (0, 240)}
CHOICES = {"preset": PRESETS, "quality": QUALITIES, "capture_backend": CAPTURE_BACKENDS, "player": PLAYERS,
           "container": CONTAINERS}

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
        cfg["schema_version"] = SCHEMA_VERSION
    return cfg

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
    out["private_apps"] = sorted({str(a).strip().lower() for a in out["private_apps"] if str(a).strip()})
    return out

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
