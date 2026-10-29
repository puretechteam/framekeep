# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License
"""Measure every preset on the user's own screen. Works only in AppData, never in the recordings folder."""

import datetime, json, os, re, shutil, subprocess, threading, time
from . import winproc
from .engine import (FFmpeg, POPEN_FLAGS, PRESET_CHAINS, PRESET_INFO, cap_kbps, capture_args, encoder_args, output_pix_fmt,
                     target_size, video_filters)
from .log import log
from .monitors import list_monitors, select_monitors
from .paths import BENCHMARK_DIR, BENCHMARK_FILE

CPU_TARGET = 15.0       # % of the whole machine a live preset may use
REALTIME_MARGIN = 2.0   # offline encode must run this many times faster than real time
QUALITY_FLOOR = 93.0    # VMAF a recommendation must reach (above ~93 most people can't tell it from the source)
RAM_SHARE = 0.25        # an encoder may use at most this share of the PC's memory
_running = threading.Lock()  # the window can close (and reopen) while a benchmark keeps running

def capture_sample(ff, cfg, seconds, monitor):
    os.makedirs(BENCHMARK_DIR, exist_ok=True)
    src = os.path.join(BENCHMARK_DIR, "sample.mkv")
    backend = "ddagrab" if monitor.dxgi_output is not None and cfg["capture_backend"] != "gdigrab" else "gdigrab"
    cap_in, (sw, sh) = capture_args(cfg, monitor, backend)
    tw, th = target_size(sw, sh, cfg["downscale_factor"])
    # the live filter chain, stopping at the 8-bit frames it hands to the encoder (stored losslessly)
    chain = video_filters(cfg, tw, th, "gray" if cfg["grayscale"] else "yuv420p", hw_frames=backend == "ddagrab")
    p = ff.run(["-y", *cap_in, "-t", str(seconds), "-vf", chain, "-fps_mode", "passthrough", "-c:v", "ffv1", src],
               timeout=seconds + 60)
    if p.returncode != 0:
        raise RuntimeError(f"Sample capture failed: {p.stderr.strip()[-300:]}")
    return src, (tw, th)

def encode_one(ff, cfg, src, encoder, seconds, vmaf, size):
    """Encode the sample exactly as a live recording would (same quality and bitrate cap)."""
    out = os.path.join(BENCHMARK_DIR, f"{encoder}.mp4")
    cap = cap_kbps(*size, cfg["fps"], encoder) if cfg["bitrate_cap"] else None
    args = encoder_args(encoder, cfg["quality"], cfg["fps"], cap)
    vf = f"format={output_pix_fmt(args)}"
    t = time.time()
    p = subprocess.Popen([ff.ffmpeg, "-hide_banner", "-y", "-benchmark", "-i", src, "-vf", vf, "-fps_mode", "passthrough", *args, out],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace",
                         creationflags=POPEN_FLAGS)
    try:
        _, stderr = p.communicate(timeout=3600)
    except subprocess.TimeoutExpired:
        p.kill()
        return None
    wall = time.time() - t
    if p.returncode != 0:
        return None
    m = re.search(r"utime=([\d.]+)s\s+stime=([\d.]+)s", stderr)
    cpu = float(m.group(1)) + float(m.group(2)) if m else wall
    ram = re.search(r"maxrss=(\d+)KiB", stderr)
    size = os.path.getsize(out)
    score = None
    if vmaf:
        v = ff.run(["-i", out, "-i", src, "-lavfi",
                    "[0:v]format=yuv420p,setpts=PTS-STARTPTS[d];[1:v]format=yuv420p,setpts=PTS-STARTPTS[r];[d][r]libvmaf=n_threads=4",
                    "-f", "null", "-"], timeout=3600)
        s = re.search(r"VMAF score: ([\d.]+)", v.stderr)
        score = round(float(s.group(1)), 1) if s else None
    kbps = size * 8 / seconds / 1000
    return {"encoder": encoder, "kbps": round(kbps, 1), "mb_per_hour": round(kbps * 3600 / 8 / 1000, 1),
            "cpu_percent": round(cpu / seconds / (os.cpu_count() or 1) * 100, 1), "realtime": round(seconds / max(wall, 0.01), 1),
            "vmaf": score, "ram_mb": round(int(ram.group(1)) / 1024) if ram else None}

def ram_limit_mb():
    return winproc.physical_memory() / 2**20 * RAM_SHARE or float("inf")

def problem(r, ram_limit=None):
    """Why a result can't be recommended: "heavy", "quality", or None if it qualifies."""
    if r["cpu_percent"] > CPU_TARGET or r["realtime"] < REALTIME_MARGIN:
        return "heavy"
    if (r.get("ram_mb") or 0) > (ram_limit if ram_limit is not None else ram_limit_mb()):
        return "heavy"
    if r["vmaf"] is not None and r["vmaf"] < QUALITY_FLOOR:
        return "quality"
    return None

def recommend(results):
    """Smallest preset that runs comfortably live and still looks good."""
    fits = [(r["kbps"], p) for p, r in results.items() if problem(r) is None]
    return min(fits)[1] if fits else "compatible"

def reasons(results, recommended):
    """Per preset: (badge, explanation) saying why it was or wasn't picked."""
    best = results.get(recommended, {})
    out = {}
    for p, r in results.items():
        issue = problem(r)
        if p == recommended:
            out[p] = ("Recommended", "Smallest file that runs comfortably and still looks good")
        elif issue == "heavy" and r["cpu_percent"] <= CPU_TARGET and r["realtime"] >= REALTIME_MARGIN:
            out[p] = ("Too heavy", f"Needs {r['ram_mb']} MB of memory, more than {RAM_SHARE:.0%} of this PC's RAM")
        elif issue == "heavy":
            out[p] = ("Too heavy", f"Uses {r['cpu_percent']:.1f}% CPU and runs {r['realtime']:.1f}× real time "
                                   f"(needs ≤ {CPU_TARGET:.0f}% and ≥ {REALTIME_MARGIN:.0f}×)")
        elif issue == "quality":
            out[p] = ("Lower quality", f"Quality {r['vmaf']:.1f} is below {QUALITY_FLOOR:.0f} on this sample")
        else:
            out[p] = ("Bigger", f"{r['mb_per_hour'] - best.get('mb_per_hour', 0):.0f} MB/hr bigger than the recommendation")
    return out

def explain(report):
    """One sentence on why the recommendation won, naming any smaller format that lost and why."""
    results, rec = report["results"], report["recommended"]
    name = PRESET_INFO[rec][0]
    r = results.get(rec)
    if not r:
        return f"{name} is the safe default."
    text = f"{name} is recommended: at {r['mb_per_hour']:.0f} MB/hr it's the smallest format that runs comfortably on your PC"
    text += f" and keeps quality at {QUALITY_FLOOR:.0f} or above." if r["vmaf"] is not None else "."
    beaten = [(o["mb_per_hour"], p) for p, o in results.items() if o["mb_per_hour"] < r["mb_per_hour"]]
    why = reasons(results, rec)
    for _, p in sorted(beaten):
        text += f" {PRESET_INFO[p][0]} is smaller, but {why[p][1][0].lower() + why[p][1][1:]}."
    return text

def is_running():
    return _running.locked()

def run(cfg, seconds=30, progress=lambda text, frac: None):
    if not _running.acquire(blocking=False):
        raise RuntimeError("A benchmark is already running")
    try:
        return _run(cfg, seconds, progress)
    finally:
        _running.release()

def _run(cfg, seconds, progress):
    ff = FFmpeg(cfg["ffmpeg_path"])
    if not ff.ffmpeg:
        raise RuntimeError("ffmpeg not found")
    monitor = select_monitors(cfg["monitors"], list_monitors())[0]
    shutil.rmtree(BENCHMARK_DIR, ignore_errors=True)
    progress(f"Capturing {seconds}s of your screen. Use your PC normally (scroll, type, switch windows)", 0.0)
    src, size = capture_sample(ff, cfg, seconds, monitor)
    vmaf = "libvmaf" in ff.run(["-filters"]).stdout
    presets = [(p, next((e for e in chain if ff.works(e)), None)) for p, chain in PRESET_CHAINS.items()]
    presets = [(p, e) for p, e in presets if e]
    results = {}
    for i, (preset, encoder) in enumerate(presets):
        progress(f"Encoding with {PRESET_INFO[preset][0]} ({PRESET_INFO[preset][1]})", 0.2 + 0.8 * i / len(presets))
        r = encode_one(ff, cfg, src, encoder, seconds, vmaf, size)
        if r:
            results[preset] = r
            log("BENCH", f"{PRESET_INFO[preset][0]:<10} {r['mb_per_hour']:>7} MB/hr  CPU {r['cpu_percent']}%  "
                         f"RAM {r['ram_mb']} MB  VMAF {r['vmaf']}", "MAGENTA")
    shutil.rmtree(BENCHMARK_DIR, ignore_errors=True)
    report = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "seconds": seconds, "fps": cfg["fps"],
              "downscale_factor": cfg["downscale_factor"], "quality": cfg["quality"], "results": results,
              "recommended": recommend(results)}
    with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    progress("Done", 1.0)
    return report

def load_report():
    try:
        with open(BENCHMARK_FILE, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return None
    report["recommended"] = recommend(report["results"])  # re-judge older reports with the current rules
    return report
