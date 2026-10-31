# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""What cameras cost (dev tool, needs `pip install psutil` and a full FFmpeg on PATH).

  python tools/measure_cameras.py all --out docs/perf/cameras.json [--sample FILE] [--seconds 60] [--ffmpeg ffmpeg/ffmpeg.exe]

The camera sample is a fixed 2-minute 720p MJPEG webcam clip (build/perf/cameras/sample.mkv). If it's missing, it is
recorded once from the first DirectShow camera (point it at a scene you're happy to keep on disk; it stays in build/,
which git ignores). Parts, each fed through FrameKeep's real camera command (framekeep.engine.build_camera_cmd) in
real time, one process at a time:
  formats   every format at 720p 15 FPS: MB per hour, CPU % of the PC and memory, plus denoise off and quiet saver
  copy      a network camera's own stream (H.264 1080p, 2 s keyframes) copied vs made smaller
  motion    detection on the sample with a person-sized object overlaid at known times: events found vs truth,
            false events on the untouched sample, and Python CPU per frame
  station   three cameras at once, then the same limited to 2 CPU cores, checking they keep real time
  quality   offline, 60 s of the sample (denoised as recorded) per codec and quality level, plus SVT-AV1 variants:
            MB per hour, peak memory, CPU time and VMAF against the undenoised original (so the denoise itself
            counts against every format; scores top out around 88-95)
Takes about 15 minutes with --seconds 60, plus about 15 for quality. Everything is written under build/perf/cameras.
"""

import argparse, json, os, re, shutil, statistics, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from framekeep import cameras, config, engine, motion

FULL = shutil.which("ffmpeg")      # preparing samples and scoring (VMAF): needs a full FFmpeg
PRODUCT = FULL                     # the camera recordings themselves: --ffmpeg puts FrameKeep's own build here
OUT = os.path.join(ROOT, "build", "perf", "cameras")
SAMPLE = os.path.join(OUT, "sample.mkv")
FORMATS = ("compatible", "smaller", "smallest", "ultra", "lowcpu")

def record_sample(seconds=120):
    ff = engine.FFmpeg(FULL)
    video = cameras.list_devices(ff)["video"]
    if not video:
        sys.exit("No camera found to record the sample from; pass --sample FILE")
    name, dev = video[0]
    print(f"Recording a {seconds} s sample from {name}…")
    os.makedirs(OUT, exist_ok=True)
    subprocess.run([FULL, "-hide_banner", "-loglevel", "error", "-f", "dshow", "-rtbufsize", "64M", "-vcodec", "mjpeg",
                    "-video_size", "1280x720", "-framerate", "30", "-i", f"video={dev}", "-t", str(seconds),
                    "-c:v", "copy", SAMPLE], check=True)

def measure_proc(cmd, seconds, affinity=None):
    """Run cmd (stdout drained like the station's reader) for `seconds` or until it ends: CPU % of the PC, mean
    private MB, wall time."""
    import psutil, threading
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    threading.Thread(target=lambda: [None for _ in iter(lambda: p.stdout.read(1 << 16), b"")], daemon=True).start()
    ps = psutil.Process(p.pid)
    if affinity:
        ps.cpu_affinity(affinity)
    mem, t0 = [], time.time()
    cpu0 = sum(ps.cpu_times()[:2])
    cpu1 = cpu0
    while time.time() - t0 < seconds and p.poll() is None:
        time.sleep(1)
        try:
            cpu1 = sum(ps.cpu_times()[:2])
            mem.append(ps.memory_info().private)
        except psutil.Error:
            break
    wall = time.time() - t0
    if p.poll() is None:
        p.stdin.write(b"q"); p.stdin.flush(); p.stdin.close()
        p.wait(30)
    return {"cpu_percent": round((cpu1 - cpu0) / wall / os.cpu_count() * 100, 2),
            "cpu_percent_of_one_core": round((cpu1 - cpu0) / wall * 100, 1),
            "private_mb": round(statistics.mean(mem) / 2**20, 1) if mem else None, "wall_s": round(wall, 1)}

def run_camera(name, cam, preset, inputs, seconds, container="mkv", affinity=None):
    """One camera recording into its own folder; returns the measurement plus MB per hour and recorded seconds."""
    folder = os.path.join(OUT, name)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(os.path.join(folder, time.strftime("%Y-%m-%d")))
    cfg = config.validate(dict(config.DEFAULT_CONFIG) | {"chunk_minutes": 60})
    encoder = engine.FFmpeg(PRODUCT).resolve(preset)[1] if preset else None
    pattern = os.path.join(folder, "%Y-%m-%d", f"%H-%M-%S.{container}")
    cmd = engine.build_camera_cmd(PRODUCT, cam, cfg, encoder, pattern, inputs)
    m = measure_proc(cmd, seconds, affinity)
    files = [os.path.join(d, f) for d, _, fs in os.walk(folder) for f in fs]
    size = sum(os.path.getsize(f) for f in files)
    dur = sum(engine.FFmpeg(FULL).probe(f).get("duration") or 0 for f in files)
    m |= {"encoder": encoder or "copy", "recorded_s": round(dur, 1),
          "mb_per_hour": round(size / 1e6 / dur * 3600, 1) if dur else None}
    print(f"  {name}: {m}", flush=True)
    return m

def device_cam(**kw):
    return config.validate_camera({"name": "Sample", "kind": "device", "device": "x", "fps": 15, "height": 720} | kw)

def formats(seconds):
    res = {}
    loop = ["-re", "-stream_loop", "-1", "-i", SAMPLE]
    ff = engine.FFmpeg(PRODUCT)
    for preset in FORMATS:
        if ff.resolve(preset)[0] != preset:
            continue
        res[preset] = run_camera(f"fmt-{preset}", device_cam(), preset, loop, seconds)
    smallest = ff.resolve("smallest")[0]
    if smallest:
        saved = engine.CAMERA_DENOISE
        engine.CAMERA_DENOISE = "null"
        try:
            res["smallest, no denoise"] = run_camera("fmt-nodenoise", device_cam(), "smallest", loop, seconds)
        finally:
            engine.CAMERA_DENOISE = saved
        res["smallest, quiet saver"] = run_camera("fmt-quiet", device_cam(quiet_saver=True), "smallest", loop, seconds)
        res["smallest, timestamp"] = run_camera("fmt-ts", device_cam(timestamp=True), "smallest", loop, seconds)
    return res

def copy_mode(seconds):
    src = os.path.join(OUT, "ipcam.mp4")
    if not os.path.exists(src):     # what a typical IP camera sends: H.264 1080p, a keyframe every 2 s
        subprocess.run([FULL, "-hide_banner", "-loglevel", "error", "-y", "-i", SAMPLE, "-vf", "fps=15,scale=1920:1080",
                        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M", "-g", "30", src], check=True)
    loop = ["-re", "-stream_loop", "-1", "-i", src]
    url = config.validate_camera({"name": "Net", "kind": "url", "url": "rtsp://x", "fps": 15, "height": 720})
    return {"copy (camera's own stream)": run_camera("net-copy", url, None, loop, seconds),
            "make smaller (smallest)": run_camera("net-smaller", dict(url, keep_stream=False), "smallest", loop, seconds)}

def motion_check():
    truth_clip = os.path.join(OUT, "motion_truth.mp4")
    subprocess.run([FULL, "-v", "error", "-y", "-i", SAMPLE, "-f", "lavfi", "-i", "color=c=0x303030:s=160x420:r=30",
                    "-filter_complex", "[0:v]scale=640:360[b];[1:v]scale=80:210[p];[b][p]overlay="
                    "x='if(between(t,20,26),(t-20)*90,if(between(t,60,63),560-(t-60)*150,-200))':y=100:shortest=1",
                    "-t", "90", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", truth_clip], check=True)
    W, H = engine.ANALYSIS_W, engine.ANALYSIS_H
    vf = (f"fps={engine.ANALYSIS_FPS},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=area,"
          f"pad={W}:{H}:-1:-1,format=rgb24")
    out = {"truth": [[20, 26], [60, 63]], "object": "80x210 on 640x360 (7% of the picture)"}
    for label, clip in (("with object", truth_clip), ("untouched sample", SAMPLE)):
        raw = subprocess.run([FULL, "-v", "error", "-i", clip, "-t", "90", "-vf", vf, "-f", "rawvideo", "pipe:1"],
                             capture_output=True).stdout
        frames = [raw[i:i + W * H * 3] for i in range(0, len(raw) - W * H * 3 + 1, W * H * 3)]
        for sens in (3, 5, 7, 10):
            events = []
            d = motion.MotionDetector(W, H, sens, on_end=lambda s, e, p: events.append([round(s, 1), round(e, 1)]))
            t0 = time.process_time()
            for i, f in enumerate(frames):
                d.feed(f, i / engine.ANALYSIS_FPS)
            cpu = time.process_time() - t0
            d.close()
            out.setdefault(label, {})[f"sensitivity {sens}"] = events
        out["python_ms_per_frame"] = round(cpu / len(frames) * 1000, 3)
        out["python_cpu_percent_of_one_core"] = round(cpu / len(frames) * engine.ANALYSIS_FPS * 100, 3)
    print("  motion:", out, flush=True)
    return out

def station(seconds):
    import threading
    res = {}
    for label, affinity in (("3 cameras", None), ("3 cameras on 2 cores", [0, 1])):
        loop = ["-re", "-stream_loop", "-1", "-i", SAMPLE]
        results = [None] * 3
        threads = [threading.Thread(target=lambda i=i: results.__setitem__(
            i, run_camera(f"station-{i}", device_cam(), "smallest", loop, seconds, affinity=affinity))) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        res[label] = {"cpu_percent": round(sum(r["cpu_percent"] for r in results), 2),
                      "private_mb": round(sum(r["private_mb"] for r in results), 1),
                      "recorded_s": [r["recorded_s"] for r in results], "wall_s": [r["wall_s"] for r in results]}
        print(f"  {label}: {res[label]}", flush=True)
    return res

def quality():
    import psutil
    ref = os.path.join(OUT, "ref.mkv")
    subprocess.run([FULL, "-v", "error", "-y", "-i", SAMPLE, "-t", "60", "-vf", "fps=15,scale=1280:720", "-c:v", "ffv1",
                    ref], check=True)
    def svt(levels, extra=""):
        args = engine.encoder_args("libsvtav1", "balanced", 15, camera=True)
        args[-1] = re.sub(r"hierarchical-levels=\d", f"hierarchical-levels={levels}", args[-1]) + extra
        return args
    variants = {f"{enc} {q}": engine.encoder_args(enc, q, 15, camera=True)
                for q in ("high", "balanced", "saver") for enc in ("libx264", "libx265", "libsvtav1")}
    variants |= {"libsvtav1 balanced, 4 levels": svt(4), "libsvtav1 balanced, 2 levels": svt(2),
                 "libsvtav1 balanced, low delay": svt(3, ":pred-struct=1"),
                 "libsvtav1 balanced, lookahead 0": svt(3, ":lookahead=0")}
    res = {}
    for name, args in variants.items():
        out = os.path.join(OUT, "q-" + re.sub(r"\W+", "_", name) + ".mkv")
        p = subprocess.Popen([PRODUCT, "-v", "error", "-y", "-i", ref, "-vf",
                              f"{engine.CAMERA_DENOISE},format={engine.output_pix_fmt(args)}", *args, out],
                             stderr=subprocess.DEVNULL)
        ps, peak, cpu = psutil.Process(p.pid), 0, 0
        while p.poll() is None:
            try:
                peak, cpu = max(peak, ps.memory_info().private), sum(ps.cpu_times()[:2])
            except psutil.Error:
                break
            time.sleep(0.2)
        log = subprocess.run([FULL, "-hide_banner", "-i", out, "-i", ref, "-lavfi",
                              "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]libvmaf=n_threads=4", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        m = re.search(r"VMAF score: ([\d.]+)", log)
        res[name] = {"mb_per_hour": round(os.path.getsize(out) / 1e6 * 60, 1), "peak_mb": round(peak / 2**20),
                     "cpu_s": round(cpu, 1), "vmaf": round(float(m.group(1)), 2) if m else None}
        print(f"  {name}: {res[name]}", flush=True)
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=["formats", "copy", "motion", "station", "quality", "all"])
    ap.add_argument("--out", default=os.path.join(OUT, "cameras.json"))
    ap.add_argument("--sample")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--ffmpeg", help="ffmpeg.exe that records (for example FrameKeep's bundled build); default: PATH")
    a = ap.parse_args()
    global SAMPLE, PRODUCT
    if a.ffmpeg:
        PRODUCT = a.ffmpeg
    if a.sample:
        SAMPLE = a.sample
    elif not os.path.exists(SAMPLE):
        record_sample()
    report = json.load(open(a.out)) if os.path.exists(a.out) else {}
    def version(ff):     # the version line, never the path (it holds the user name)
        return subprocess.run([ff, "-hide_banner", "-version"], capture_output=True, text=True).stdout.splitlines()[0].split(" Copyright")[0]
    report["machine"] = {"cpus": os.cpu_count(), "ffmpeg": version(PRODUCT), "helpers": version(FULL)}
    report["sample"] = "2-minute 720p MJPEG webcam clip, still indoor scene, played in real time and looped"
    parts = ("formats", "copy", "motion", "station", "quality") if a.part == "all" else (a.part,)
    for part in parts:
        print(part, flush=True)
        if part in ("motion", "quality"):
            report[part] = motion_check() if part == "motion" else quality()
        else:
            report[part] = {"formats": formats, "copy": copy_mode, "station": station}[part](a.seconds)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(report, f, indent=1)
    print("wrote", a.out)

if __name__ == "__main__":
    main()
