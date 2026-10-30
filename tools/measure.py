# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Before/after performance measurements for FrameKeep releases (dev tool, needs `pip install psutil`).

  python tools/measure.py sample
      Renders a deterministic synthetic desktop (code editor, terminal, web page, idle time, a video and window
      switching) to build/perf/sample.mkv, so every version is measured on exactly the same pixels.
      Needs a full FFmpeg on PATH for this one step; everything else uses the bundled build.
  python tools/measure.py run --repo <checkout> --out docs/perf/<version>.json [--parts offline,pipeline,app]
      Measures the FrameKeep in <checkout> (for example a `git worktree` of an older tag) with this repo's
      bundled FFmpeg:
        offline   each preset's real recording command on the sample, as fast as possible:
                  size per hour, VMAF, CPU time, FFmpeg peak memory, speed
        pipeline  the same command fed the sample in real time (-re), sampling FFmpeg's private memory and CPU
        app       the FrameKeep process itself: in the tray, with the window open, and recording
  python tools/measure.py compare docs/perf/3.0.0.json docs/perf/3.1.0.json
      Prints a Markdown before/after table.

Everything is written under build/perf (a private APPDATA, outputs and recordings of the app test), never to
the real FrameKeep data folder or any recordings folder.
"""

import argparse, json, os, platform, re, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERF = os.path.join(ROOT, "build", "perf")
SAMPLE = os.path.join(PERF, "sample.mkv")
FFMPEG = os.path.join(ROOT, "ffmpeg", "ffmpeg.exe")
W, H, FPS, SECONDS = 1920, 1080, 5, 120
NO_WINDOW = 0x08000000

# ---- synthetic desktop sample ------------------------------------------------------------------------------

def make_sample():
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    fonts = r"C:\Windows\Fonts"
    mono = ImageFont.truetype(os.path.join(fonts, "CascadiaMono.ttf"), 15)
    ui = ImageFont.truetype(os.path.join(fonts, "segoeui.ttf"), 17)
    ui_big = ImageFont.truetype(os.path.join(fonts, "segoeuib.ttf"), 34)
    code = []
    for name in sorted(os.listdir(os.path.join(ROOT, "framekeep"))):
        if name.endswith(".py"):
            with open(os.path.join(ROOT, "framekeep", name), encoding="utf-8") as f:
                code += f.read().splitlines()
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        prose = [l for l in f.read().splitlines() if len(l) > 40 and not l.startswith(("|", "#", "!", "`"))]
    photo = Image.effect_mandelbrot((520, 300), (-2.2, -1.1, 0.8, 1.1), 60).convert("RGB")
    typed = ("def resolution_for(monitor, factor):\n    \"\"\"Target size for one display.\"\"\"\n"
             "    _, (_, _, w, h) = capture_region(monitor, False)\n    return target_size(w, h, factor)\n") * 3

    def chrome(img, title):
        d = ImageDraw.Draw(img)
        d.rectangle((0, H - 48, W, H), fill=(32, 32, 36))
        for i in range(8):
            d.rounded_rectangle((760 + i * 52, H - 40, 792 + i * 52, H - 8), 6, fill=(70 + i * 12, 90, 140 - i * 8))
        d.text((W - 110, H - 38), "10:42 AM", font=ui, fill=(230, 230, 230))
        d.rectangle((0, 0, W, 34), fill=(43, 43, 48))
        d.text((14, 6), title, font=ui, fill=(220, 220, 220))
        return d

    def editor(top, extra="", cursor=True):
        img = Image.new("RGB", (W, H), (30, 31, 34))
        d = chrome(img, "engine.py - framekeep - Code")
        d.rectangle((0, 34, 260, H - 48), fill=(37, 38, 41))
        for i, name in enumerate(sorted(os.listdir(os.path.join(ROOT, "framekeep")))):
            d.text((24, 50 + i * 24), name, font=ui, fill=(190, 190, 190) if i != 3 else (255, 255, 255))
        lines = code[top:top + 56] + extra.split("\n")
        for i, line in enumerate(lines[:56]):
            y = 44 + i * 18
            d.text((280, y), f"{top + i + 1:>4}", font=mono, fill=(110, 110, 120))
            color = (198, 120, 221) if line.lstrip().startswith(("def ", "class ", "import", "from", "return")) else \
                    (152, 195, 121) if '"' in line else (220, 223, 228)
            d.text((340, y), line, font=mono, fill=color)
        if cursor:
            last = len(lines[:56]) - 1
            d.rectangle((340 + 9 * len(lines[last]), 44 + last * 18, 342 + 9 * len(lines[last]), 60 + last * 18), fill=(255, 255, 255))
        return img

    def terminal(n):
        img = Image.new("RGB", (W, H), (12, 12, 12))
        d = chrome(img, "Windows PowerShell")
        out = [f"tests/test_engine.py::test_case_{i:03d} PASSED" if i % 7 else f"[{i:05d}] encoder: frame={i * 3} q=31.0 size={i * 17}kB"
               for i in range(n)]
        for i, line in enumerate(out[-55:]):
            d.text((16, 44 + i * 18), line, font=mono, fill=(204, 204, 204) if "PASSED" not in line else (110, 200, 120))
        return img

    def web(scroll, video=None):
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = chrome(img, "FrameKeep - Docs - Browser")
        d.rectangle((0, 34, W, 80), fill=(241, 243, 244))
        d.rounded_rectangle((300, 42, 1600, 72), 15, fill=(255, 255, 255))
        d.text((320, 46), "https://example.org/framekeep/docs", font=ui, fill=(60, 60, 60))
        y = 110 - scroll
        for block in range(30):
            if y > H:
                break
            if block % 5 == 0:
                d.text((360, y), f"Section {block // 5 + 1}: How it works", font=ui_big, fill=(20, 20, 20))
                y += 60
            elif block % 5 == 3:
                img.paste(photo, (360, y))
                d.rectangle((920, y, 1560, y + 300), fill=(232, 240, 254))
                d.text((940, y + 20), prose[block % len(prose)][:60], font=ui, fill=(30, 60, 120))
                y += 330
            else:
                for line in range(5):
                    d.text((360, y), prose[(block * 5 + line) % len(prose)][:120], font=ui, fill=(50, 50, 50))
                    y += 28
                y += 20
        if video is not None:
            img.paste(video, (480, 200))
        return img

    def cursor(img, x, y):
        d = ImageDraw.Draw(img)
        d.polygon([(x, y), (x, y + 22), (x + 6, y + 17), (x + 11, y + 26), (x + 14, y + 24), (x + 9, y + 15), (x + 17, y + 15)],
                  fill=(255, 255, 255), outline=(0, 0, 0))
        return img

    def frame(f):
        if f < 50:   return cursor(editor(f * 3), 900, 400)                                   # scrolling code
        if f < 75:   return editor(150, cursor=(f // 3) % 2 == 0)                             # reading (cursor blink)
        if f < 150:  return cursor(editor(150, typed[:(f - 75) * 3]), 1200, 300)              # typing
        if f < 275:  return terminal((f - 150) * 2 + 1)                                       # test/log output
        if f < 400:  return cursor(web((f - 275) * 8), 1400 - (f - 275) * 4, 500)            # reading a web page
        if f < 475:  return cursor(web(1000), 900, 500)                                       # idle
        if f < 550:                                                                           # a video playing
            z = 1.5 * 0.97 ** (f - 475)
            clip = Image.effect_mandelbrot((960, 540), (-0.745 - z, 0.11 - z * 0.56, -0.745 + z, 0.11 + z * 0.56), 80)
            return web(1000, ImageOps.colorize(clip, (0, 16, 48), (255, 204, 102), (200, 40, 90)))
        return [editor(150, typed), terminal(250), web(400)][(f // 5) % 3]                    # switching windows

    # FrameKeep's minimal FFmpeg has no raw video demuxer, so piping frames in needs a full build on PATH
    full = shutil.which("ffmpeg")
    if not full or os.path.samefile(full, FFMPEG):
        sys.exit("Making the sample needs a full FFmpeg build on PATH (for example: winget install Gyan.FFmpeg)")
    os.makedirs(PERF, exist_ok=True)
    p = subprocess.Popen([full, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgr0", SAMPLE],
                         stdin=subprocess.PIPE, creationflags=NO_WINDOW)
    for f in range(FPS * SECONDS):
        p.stdin.write(frame(f).tobytes())
        if f % 50 == 0:
            print(f"  frame {f}/{FPS * SECONDS}", flush=True)
    p.stdin.close()
    p.wait()
    print(f"Wrote {SAMPLE} ({os.path.getsize(SAMPLE) / 1e6:.0f} MB)")

# ---- measuring a checkout ------------------------------------------------------------------------------------

def load_framekeep(repo):
    """Import the framekeep package from another checkout, with a private APPDATA."""
    os.environ["APPDATA"] = os.path.join(PERF, "appdata")
    os.makedirs(os.environ["APPDATA"], exist_ok=True)
    sys.path.insert(0, os.path.abspath(repo))
    import framekeep, framekeep.config, framekeep.engine, framekeep.monitors
    assert os.path.dirname(framekeep.__file__).startswith(os.path.abspath(repo)), "imported the wrong framekeep"
    return framekeep

def recording_cmd(fk, cfg, encoder, out, realtime):
    """That version's exact live recording command, with the screen capture input swapped for the sample."""
    mon = fk.monitors.Monitor(1, r"\\.\DISPLAY1", (0, 0, W, H), (0, 0, W, H - 48), True, None)
    cmd = fk.engine.build_record_cmd(FFMPEG, cfg, mon, encoder, "gdigrab", out)
    start = cmd.index("gdigrab") - 1
    end = cmd.index("desktop", start) + 1
    cmd[start:end] = (["-re"] if realtime else []) + ["-i", SAMPLE]
    if not realtime:
        cmd[cmd.index("-loglevel") + 1] = "info"
        cmd.insert(1, "-benchmark")
    return cmd

def presets(fk):
    ff = fk.engine.FFmpeg(os.path.dirname(FFMPEG))
    return {p: e for p, e in ff.available_presets().items() if e}

FFPROBE = os.path.join(ROOT, "ffmpeg", "ffprobe.exe")

def presentation_times(path):
    """When each decoded frame is shown, as FFmpeg-based players (FrameKeep's built-in player included) decide it,
    starting at 0."""
    p = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time",
                        "-of", "csv=p=0", path], capture_output=True, text=True, creationflags=NO_WINDOW)
    times = [float(t.strip(",")) for t in p.stdout.split() if t.strip(",") not in ("", "N/A")]
    return [t - times[0] for t in times] if times else []

def reference_times(fk, cfg, tw, th, out_dir):
    """When each frame kept by that version's filter chain should be shown (stored losslessly, then read back)."""
    ref = os.path.join(out_dir, "reference.mkv")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", SAMPLE, "-vf", fk.engine.video_filters(cfg, tw, th, "yuv420p"),
                    "-fps_mode", "passthrough", "-c:v", "ffv1", ref], check=True, creationflags=NO_WINDOW)
    times = presentation_times(ref)
    os.remove(ref)
    return times

def timing_errors(got, want):
    """Frames shown more than 10 ms away from when they were captured (or missing/extra frames)."""
    return sum(1 for g, w in zip(got, want) if abs(g - w) > 0.01) + abs(len(got) - len(want))

def offline(fk, cfg):
    tw, th = fk.engine.target_size(W, H, cfg["downscale_factor"])
    out_dir = os.path.join(PERF, "out")
    os.makedirs(out_dir, exist_ok=True)
    want = reference_times(fk, cfg, tw, th, out_dir)
    results = {}
    for preset, encoder in presets(fk).items():
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir)
        out = os.path.join(out_dir, f"{preset}.mp4")
        cmd = recording_cmd(fk, cfg, encoder, out, realtime=False)
        t = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW)
        wall = time.time() - t
        if p.returncode != 0:
            print(f"  {preset}: failed: {p.stderr.strip()[-300:]}")
            continue
        m = re.search(r"utime=([\d.]+)s\s+stime=([\d.]+)s", p.stderr)
        rss = re.search(r"maxrss=(\d+)KiB", p.stderr)
        cpu = float(m.group(1)) + float(m.group(2))
        # both sides at a constant 5 FPS, so every captured frame is scored against what a viewer sees at that
        # moment (the last kept frame), which also counts skipped frames and wrong timestamps against the result
        v = subprocess.run([FFMPEG, "-hide_banner", "-i", out, "-i", SAMPLE, "-lavfi",
                            f"[0:v]setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[d];"
                            f"[1:v]setpts=PTS-STARTPTS,scale={tw}:{th}:flags=area,format=yuv420p[r];[d][r]libvmaf=n_threads=4",
                            "-f", "null", "-"], capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW)
        s = re.search(r"VMAF score: ([\d.]+)", v.stderr)
        size = os.path.getsize(out)
        results[preset] = {"encoder": encoder, "mb_per_hour": round(size / SECONDS * 3600 / 1e6, 1),
                           "vmaf": round(float(s.group(1)), 2) if s else None,
                           "cpu_seconds": round(cpu, 2), "cpu_percent": round(cpu / SECONDS / os.cpu_count() * 100, 2),
                           "ffmpeg_peak_mb": round(int(rss.group(1)) / 1024, 1) if rss else None,
                           "realtime": round(SECONDS / wall, 1),
                           "frames": len(want), "timing_errors": timing_errors(presentation_times(out), want)}
        print(f"  offline {preset:<10} {results[preset]}", flush=True)
    return results

def watch(proc, seconds, every=1.0, children=False):
    """Private memory (mean/peak MB), CPU % of the whole PC and context switches/s of a process over `seconds`."""
    import psutil
    ps = psutil.Process(proc.pid)
    # A venv's python.exe on Windows is a launcher that runs the real interpreter as its child: measure that one
    real = [k for k in ps.children() if k.name().lower().startswith("python")]
    if real and ps.memory_info().private < 8 * 2**20:
        ps = real[0]
    def procs():
        out = [ps]
        if children:
            out += ps.children(recursive=True)
        return out
    def totals():
        cpu = ctx = 0
        for q in procs():
            try:
                t, c = q.cpu_times(), q.num_ctx_switches()
                cpu += t.user + t.system
                ctx += c.voluntary + c.involuntary
            except psutil.Error:
                pass
        return cpu, ctx
    mem = []
    cpu0, ctx0 = cpu1, ctx1 = totals()
    t0 = t1 = time.time()
    ws = []
    while time.time() - t0 < seconds and proc.poll() is None:
        time.sleep(every)
        private = working = 0
        for q in procs():
            try:
                m = q.memory_info()
                private, working = private + m.private, working + m.rss
            except psutil.Error:
                pass
        if proc.poll() is not None:
            break       # it exited mid-sample; keep the last complete reading
        mem.append(private / 2**20)
        ws.append(working / 2**20)
        cpu1, ctx1 = totals()
        t1 = time.time()
    wall = max(t1 - t0, 0.001)
    # private = memory committed to the process (what it has reserved RAM/pagefile for);
    # working set = what is actually in RAM right now (Task Manager's Details tab shows both)
    return {"private_mb_mean": round(sum(mem) / max(1, len(mem)), 1), "private_mb_peak": round(max(mem, default=0), 1),
            "working_set_mb_mean": round(sum(ws) / max(1, len(ws)), 1),
            "cpu_percent": round((cpu1 - cpu0) / wall / os.cpu_count() * 100, 2),
            "ctx_switches_per_s": round((ctx1 - ctx0) / wall, 1)}

def pipeline(fk, cfg):
    out_dir = os.path.join(PERF, "out")
    results = {}
    for preset, encoder in presets(fk).items():
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir)
        out = os.path.join(out_dir, f"{preset}.mp4")
        proc = subprocess.Popen(recording_cmd(fk, cfg, encoder, out, realtime=True), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        r = watch(proc, SECONDS + 30)
        proc.wait()
        r["mb_per_hour"] = round(os.path.getsize(out) / SECONDS * 3600 / 1e6, 1) if os.path.exists(out) else None
        results[preset] = r
        print(f"  pipeline {preset:<10} {r}", flush=True)
    return results

def app(repo, cfg, seconds=45, warmup=15):
    import psutil
    appdata = os.path.join(PERF, "appdata-app")
    rec = os.path.join(PERF, "rec")
    results = {}
    for state, args, record in (("tray", ["--minimized"], False), ("window", [], False), ("recording_in_tray", ["--minimized"], True)):
        shutil.rmtree(appdata, ignore_errors=True)
        shutil.rmtree(rec, ignore_errors=True)
        os.makedirs(os.path.join(appdata, "FrameKeepOS"))
        os.makedirs(rec)
        with open(os.path.join(appdata, "FrameKeepOS", "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg | {"log_root": rec, "onboarded": True, "record_on_launch": record, "ffmpeg_path": os.path.dirname(FFMPEG)}, f)
        env = os.environ | {"APPDATA": appdata, "PYTHONPATH": os.path.abspath(repo)}
        proc = subprocess.Popen([sys.executable, "-m", "framekeep", *args], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        time.sleep(warmup)
        r = {"app": watch(proc, seconds)}
        if record:
            kids = [k for k in psutil.Process(proc.pid).children(recursive=True) if "ffmpeg" in k.name().lower()]
            r["ffmpeg"] = {"private_mb": round(sum(k.memory_info().private for k in kids) / 2**20, 1),
                           "working_set_mb": round(sum(k.memory_info().rss for k in kids) / 2**20, 1)}
        for k in psutil.Process(proc.pid).children(recursive=True):
            k.kill()
        proc.kill()
        proc.wait()
        results[state] = r
        print(f"  app {state:<18} {r}", flush=True)
        time.sleep(2)
    shutil.rmtree(rec, ignore_errors=True)
    return results

def repo_label(repo):
    """The measured checkout as a path inside this repo, or just its folder name: a path outside it would carry
    the user's home folder into the published results."""
    rel = os.path.relpath(os.path.abspath(repo), ROOT)
    return os.path.basename(os.path.abspath(repo)) if rel.startswith("..") else rel.replace("\\", "/")

def run(args):
    if not os.path.exists(SAMPLE):
        sys.exit("Make the sample first: python tools/measure.py sample")
    fk = load_framekeep(args.repo)
    cfg = fk.config.validate(dict(fk.config.DEFAULT_CONFIG))
    parts = args.parts.split(",")
    report = {"version": fk.__version__, "repo": repo_label(args.repo), "date": time.strftime("%Y-%m-%d %H:%M"),
              "machine": {"cpu": platform.processor(), "threads": os.cpu_count(), "os": platform.platform()},
              "ffmpeg": subprocess.run([FFMPEG, "-hide_banner", "-version"], capture_output=True, text=True).stdout.split("\n")[0],
              "sample": {"file": os.path.relpath(SAMPLE, ROOT), "seconds": SECONDS, "fps": FPS, "size": f"{W}x{H}"},
              "settings": {k: cfg[k] for k in ("fps", "downscale_factor", "quality", "bitrate_cap", "drop_duplicates", "container")}}
    if "offline" in parts:
        report["offline"] = offline(fk, cfg)
    if "pipeline" in parts:
        report["pipeline"] = pipeline(fk, cfg)
    if "app" in parts:
        report["app"] = app(args.repo, cfg)
    if os.path.exists(args.out):   # keep parts measured in an earlier run
        with open(args.out, encoding="utf-8") as f:
            report = json.load(f) | report
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Wrote {args.out}")

# ---- comparing two reports -----------------------------------------------------------------------------------

def compare(a_path, b_path):
    with open(a_path, encoding="utf-8") as f:
        a = json.load(f)
    with open(b_path, encoding="utf-8") as f:
        b = json.load(f)
    def cell(x, y, lower_is_better=True, digits=1):
        if x is None or y is None:
            return "-"
        if not x:
            return f"{x:.{digits}f} → {y:.{digits}f}"
        pct = (y - x) / x * 100
        good = pct < 0 if lower_is_better else pct > 0
        mark = "" if abs(pct) < 1 else (" ✅" if good else " ⚠️")
        return f"{x:.{digits}f} → {y:.{digits}f} ({pct:+.0f}%){mark}"
    va, vb = a["version"], b["version"]
    out = [f"Before = {va}, after = {vb}. Same machine, same FFmpeg ({b['ffmpeg']}), same {b['sample']['seconds']} s sample.", ""]
    if "offline" in a and "offline" in b:
        out += ["### Encoding (offline, real recording command on the sample)", "",
                "| Format | File size (MB/hr) | Quality (VMAF) | CPU time (s) | FFmpeg peak RAM (MB) | Speed (× real time) | Mistimed frames |",
                "|---|---|---|---|---|---|---|"]
        for p in b["offline"]:
            x, y = a["offline"].get(p, {}), b["offline"][p]
            out.append(f"| {p} | {cell(x.get('mb_per_hour'), y['mb_per_hour'])} | {cell(x.get('vmaf'), y['vmaf'], False, 2)} "
                       f"| {cell(x.get('cpu_seconds'), y['cpu_seconds'])} | {cell(x.get('ffmpeg_peak_mb'), y['ffmpeg_peak_mb'], digits=0)} "
                       f"| {cell(x.get('realtime'), y['realtime'], False)} "
                       f"| {x.get('timing_errors', '-')} → {y.get('timing_errors', '-')} of {y.get('frames', '-')} |")
        out.append("")
    if "pipeline" in a and "pipeline" in b:
        out += ["### Live recording (sample fed in real time)", "",
                "| Format | FFmpeg RAM, mean (MB) | FFmpeg RAM, peak (MB) | FFmpeg CPU (% of PC) | File size (MB/hr) |", "|---|---|---|---|---|"]
        for p in b["pipeline"]:
            x, y = a["pipeline"].get(p, {}), b["pipeline"][p]
            out.append(f"| {p} | {cell(x.get('private_mb_mean'), y['private_mb_mean'], digits=0)} "
                       f"| {cell(x.get('private_mb_peak'), y['private_mb_peak'], digits=0)} "
                       f"| {cell(x.get('cpu_percent'), y['cpu_percent'], digits=2)} | {cell(x.get('mb_per_hour'), y['mb_per_hour'])} |")
        out.append("")
    if "app" in a and "app" in b:
        out += ["### FrameKeep app process", "",
                "| State | Committed memory (MB) | Working set (MB) | CPU (% of PC) | Thread wakeups (context switches/s) |",
                "|---|---|---|---|---|"]
        for s in b["app"]:
            x, y = a["app"].get(s, {}).get("app", {}), b["app"][s]["app"]
            out.append(f"| {s.replace('_', ' ')} | {cell(x.get('private_mb_mean'), y['private_mb_mean'], digits=0)} "
                       f"| {cell(x.get('working_set_mb_mean'), y.get('working_set_mb_mean'), digits=0)} "
                       f"| {cell(x.get('cpu_percent'), y['cpu_percent'], digits=2)} | {cell(x.get('ctx_switches_per_s'), y['ctx_switches_per_s'], digits=0)} |")
        out.append("")
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(out))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    r = sub.add_parser("run")
    r.add_argument("--repo", default=ROOT)
    r.add_argument("--out", required=True)
    r.add_argument("--parts", default="offline,pipeline,app")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    args = ap.parse_args()
    if args.cmd == "sample":
        make_sample()
    elif args.cmd == "run":
        run(args)
    else:
        compare(args.before, args.after)

if __name__ == "__main__":
    main()
