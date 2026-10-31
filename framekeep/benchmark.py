# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Measure every preset on the user's own screen. Works only in AppData, never in the recordings folder."""

import array, datetime, json, math, os, re, shutil, subprocess, threading, time
from . import winproc
from .engine import (AUDIO_INFO, AUDIO_PIPE_INPUT, FFmpeg, POPEN_FLAGS, PRESET_CHAINS, PRESET_INFO, audio_args, audio_codec,
                     cap_kbps, capture_args, encoder_args, output_pix_fmt, resolve_container, target_size, video_filters)
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

def _encode(ff, args, timeout=3600):
    """Run one ffmpeg -benchmark encode: (wall seconds, CPU seconds, peak RAM in MB or None), or None on failure."""
    t = time.time()
    p = subprocess.Popen([ff.ffmpeg, "-hide_banner", "-y", "-benchmark", *args], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace", creationflags=POPEN_FLAGS)
    winproc.own(p)
    try:
        _, stderr = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        return None
    wall = time.time() - t
    if p.returncode != 0:
        return None
    m = re.search(r"utime=([\d.]+)s\s+stime=([\d.]+)s", stderr)
    ram = re.search(r"maxrss=(\d+)KiB", stderr)
    return wall, float(m.group(1)) + float(m.group(2)) if m else wall, round(int(ram.group(1)) / 1024) if ram else None

def audio_sample(seconds):
    """A fixed stand-in for desktop sound (48 kHz float stereo), the same on every PC: a third chords and a melody,
    a third noisy speech-like bursts, a third silence. Repeats a 3 s phrase so it's quick to build in Python."""
    rate, phrase, seed = 48000, [], 12345
    for i in range(rate * 3):
        t = i / rate
        part = int(t)                       # 0 music, 1 speech-like, 2 silence
        if part == 0:
            note = 220 * 2 ** ((int(t * 4) % 5) / 12)
            v = 0.15 * (math.sin(2 * math.pi * note * t) + 0.5 * math.sin(2 * math.pi * 1.5 * note * t)
                        + 0.3 * math.sin(2 * math.pi * 880 * t) * math.exp(-6 * (t % 0.25)))
            left, right = v, v * 0.9
        elif part == 1:
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            v = (seed / 0x7FFFFFFF - 0.5) * 0.3 * max(0.0, math.sin(2 * math.pi * 4 * t))
            left = right = v + 0.05 * math.sin(2 * math.pi * 180 * t)
        else:
            left = right = 0.0
        phrase += (left, right)
    data = array.array("f", phrase).tobytes()
    reps = max(1, math.ceil(seconds / 3))
    path = os.path.join(BENCHMARK_DIR, "audio.f32")
    with open(path, "wb") as f:
        f.write(data * reps)
    return path, reps * 3

def encode_audio(ff, codec, quality, src, seconds):
    """Cost of a desktop-audio track: size, CPU and the audio encoder's RAM."""
    out = os.path.join(BENCHMARK_DIR, f"audio-{codec}.mka")
    res = _encode(ff, [*AUDIO_PIPE_INPUT, "-i", src, *audio_args(codec, quality), out], timeout=600)
    if not res:
        return None
    _, cpu, ram = res
    kbps = os.path.getsize(out) * 8 / seconds / 1000
    return {"codec": codec, "kbps": round(kbps, 1), "cpu_percent": round(cpu / seconds / (os.cpu_count() or 1) * 100, 2),
            "ram_mb": ram}

def with_audio(r, a):
    """A preset's result with its audio track's cost added (audio is encoded in its own small FFmpeg)."""
    kbps = r["kbps"] + a["kbps"]
    return r | {"kbps": round(kbps, 1), "mb_per_hour": round(kbps * 3600 / 8 / 1000, 1),
                "cpu_percent": round(r["cpu_percent"] + a["cpu_percent"], 1),
                "ram_mb": (r["ram_mb"] or 0) + (a["ram_mb"] or 0) or None, "audio": a}

def encode_one(ff, cfg, src, encoder, seconds, vmaf, size):
    """Encode the sample exactly as a live recording would (same quality and bitrate cap)."""
    out = os.path.join(BENCHMARK_DIR, f"{encoder}.mp4")
    cap = cap_kbps(*size, cfg["fps"], encoder) if cfg["bitrate_cap"] else None
    args = encoder_args(encoder, cfg["quality"], cfg["fps"], cap)
    vf = f"format={output_pix_fmt(args)}"
    res = _encode(ff, ["-i", src, "-vf", vf, "-fps_mode", "passthrough", *args, out])
    if not res:
        return None
    wall, cpu, ram = res
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
            "vmaf": score, "ram_mb": ram}

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
    if report.get("audio"):
        parts = [f"{AUDIO_INFO[c][0]} {a['kbps']:.0f} kbps" for c, a in sorted(report["audio"].items())]
        text += f" Sizes, CPU and memory include desktop audio ({', '.join(parts)} on a fixed sound clip)."
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
    results, audio = {}, {}
    if cfg["audio"]:
        wanted = {audio_codec(cfg["audio_codec"], e, resolve_container(cfg["container"], e)) for _, e in presets}
        if wanted & ff.audio_codecs():
            progress("Measuring desktop audio", 0.18)
            clip, clip_seconds = audio_sample(seconds)
            for codec in sorted(wanted & ff.audio_codecs()):
                a = encode_audio(ff, codec, cfg["quality"], clip, clip_seconds)
                if a:
                    audio[codec] = a
                    log("BENCH", f"Audio {AUDIO_INFO[codec][0]:<5} {a['kbps']:>6} kbps  CPU {a['cpu_percent']}%  "
                                 f"RAM {a['ram_mb']} MB", "MAGENTA")
    for i, (preset, encoder) in enumerate(presets):
        progress(f"Encoding with {PRESET_INFO[preset][0]} ({PRESET_INFO[preset][1]})", 0.2 + 0.8 * i / len(presets))
        r = encode_one(ff, cfg, src, encoder, seconds, vmaf, size)
        if r:
            codec = audio_codec(cfg["audio_codec"], encoder, resolve_container(cfg["container"], encoder))
            if codec in audio:
                r = with_audio(r, audio[codec])
            results[preset] = r
            log("BENCH", f"{PRESET_INFO[preset][0]:<10} {r['mb_per_hour']:>7} MB/hr  CPU {r['cpu_percent']}%  "
                         f"RAM {r['ram_mb']} MB  VMAF {r['vmaf']}", "MAGENTA")
    shutil.rmtree(BENCHMARK_DIR, ignore_errors=True)
    report = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "seconds": seconds, "fps": cfg["fps"],
              "downscale_factor": cfg["downscale_factor"], "quality": cfg["quality"], "audio": audio, "results": results,
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
