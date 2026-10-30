# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""What desktop audio costs (dev tool, needs `pip install psutil` and a full FFmpeg on PATH).

  python tools/measure_audio.py codecs --out docs/perf/audio-codecs.json
      Encodes a fixed 90 s sound sample (30 s tones and chords, 30 s noisy speech-like bursts, 30 s silence) with
      each Opus/AAC setting FrameKeep could use: bitrate per part, file size per hour, CPU time and a waveform
      fidelity check (SDR on the tonal part; noise can't be compared sample by sample, so it isn't).
  python tools/measure_audio.py live --out docs/perf/audio-live.json [--seconds 60] [--rounds 2]
      Records the docs/perf desktop sample in real time with audio off and on, alternating (ABBA), while the
      fixed sound clip plays quietly, and samples every FrameKeep process: the app (capture threads), the video
      FFmpeg, and with audio the audio encoder and the muxer. Then one audio-on run limited to 2 CPU cores, as on
      an old dual-core PC, checking it keeps up.

The video FFmpeg and the muxer are the bundled build; the audio encoder is the FFmpeg on PATH, because the
bundled build only gets Opus/AAC with FFmpeg fk4. Everything is written under build/perf.
"""

import argparse, json, os, re, shutil, statistics, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure

FULL = shutil.which("ffmpeg")
FULL_PLAY = shutil.which("ffplay")
OUT = os.path.join(measure.PERF, "audio")
PCM = ["-f", "f32le", "-sample_rate", "48000", "-ch_layout", "stereo"]
LIVE_PRESETS = ("compatible", "smallest")     # one AAC preset, one Opus preset

# ---- codec settings -------------------------------------------------------------------------------------------
SOUND = os.path.join(OUT, "sound.f32")
TONES = ("0.18*(sin(2*PI*220*pow(2,floor(mod(t*2,8))/12)*t)+0.5*sin(2*PI*330*pow(2,floor(mod(t*2,6))/12)*t)"
         "+0.3*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*0.25*t))+0.25*sin(2*PI*1760*t)*exp(-8*mod(t,0.5)))")
CODEC_VARIANTS = {
    "Opus 24k": ["-c:a", "libopus", "-b:a", "24k", "-vbr", "on", "-application", "audio", "-dtx", "1"],
    "Opus 32k": ["-c:a", "libopus", "-b:a", "32k", "-vbr", "on", "-application", "audio", "-dtx", "1"],
    "Opus 48k": ["-c:a", "libopus", "-b:a", "48k", "-vbr", "on", "-application", "audio", "-dtx", "1"],
    "Opus 96k": ["-c:a", "libopus", "-b:a", "96k", "-vbr", "on", "-application", "audio", "-dtx", "1"],
    "Opus 48k without DTX": ["-c:a", "libopus", "-b:a", "48k", "-vbr", "on", "-application", "audio"],
    "Opus 48k 60 ms frames": ["-c:a", "libopus", "-b:a", "48k", "-vbr", "on", "-application", "audio", "-dtx", "1",
                              "-frame_duration", "60"],
    "Opus 48k complexity 5": ["-c:a", "libopus", "-b:a", "48k", "-vbr", "on", "-application", "audio", "-dtx", "1",
                              "-compression_level", "5"],
    "AAC 64k": ["-c:a", "aac", "-b:a", "64k"],
    "AAC 96k": ["-c:a", "aac", "-b:a", "96k"],
    "AAC 128k": ["-c:a", "aac", "-b:a", "128k"],
    "AAC 160k": ["-c:a", "aac", "-b:a", "160k"],
    "AAC 128k fast coder": ["-c:a", "aac", "-b:a", "128k", "-aac_coder", "fast"],
}

def make_sound():
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(SOUND):
        return
    right = TONES.replace("220*", "221*").replace("330*", "331*").replace("sin(2*PI*0.25*t)", "cos(2*PI*0.25*t)")
    subprocess.run([FULL, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"aevalsrc='{TONES}|{right}':s=48000:d=30",
                    "-f", "lavfi", "-i", "anoisesrc=d=30:c=pink:r=48000:a=0.25:seed=7,highpass=f=1000,lowpass=f=12000,"
                                         "volume='0.6+0.4*sin(2*PI*5*t)*sin(2*PI*0.7*t)':eval=frame,aformat=channel_layouts=stereo",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=30",
                    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1,aformat=sample_fmts=flt:channel_layouts=stereo",
                    *PCM[:2], SOUND], check=True)

def codecs():
    make_sound()
    mux = ["-movflags", "+delay_moov+default_base_moof", "-frag_duration", "5000000"]
    res = {}
    for name, args in CODEC_VARIANTS.items():
        out = os.path.join(OUT, re.sub(r"\W+", "_", name) + ".mp4")
        cpu = []
        for _ in range(3):      # best of 3: CPU time on an otherwise idle PC
            p = subprocess.run([FULL, "-hide_banner", "-benchmark", "-y", *PCM, "-i", SOUND, *args, *mux, out],
                               capture_output=True, text=True)
            m = re.search(r"utime=([\d.]+)s\s+stime=([\d.]+)s", p.stderr)
            cpu.append(float(m.group(1)) + float(m.group(2)))
        parts = [0, 0, 0]
        pk = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "packet=pts_time,size",
                             "-of", "csv=p=0", out], capture_output=True, text=True).stdout.split()
        for line in pk:
            t, size = line.split(",")[:2]
            parts[min(2, int(max(0.0, float(t)) // 30))] += int(size)
        e = subprocess.run([FULL, "-hide_banner", "-i", out, *PCM, "-i", SOUND, "-filter_complex",
                            "[0:a]atrim=0:30,asetpts=N/SR/TB[x];[1:a]atrim=0:30,asetpts=N/SR/TB[y];[x][y]asdr", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        sdr = [float(v) for v in re.findall(r"SDR ch\d+: ([-\d.]+) dB", e)]
        res[name] = {"tones_kbps": round(parts[0] * 8 / 30000, 1), "speechlike_kbps": round(parts[1] * 8 / 30000, 1),
                     "silence_kbps": round(parts[2] * 8 / 30000, 2), "file_mb_per_hour": round(os.path.getsize(out) / 90 * 3.6e-3, 2),
                     "cpu_seconds_per_hour": round(min(cpu) * 40, 1), "sdr_tones_db": round(sum(sdr) / len(sdr), 1) if sdr else None}
        r = res[name]
        print(f"{name:<24} tones {r['tones_kbps']:>6} speech-like {r['speechlike_kbps']:>6} silence {r['silence_kbps']:>5} kbps  "
              f"{r['file_mb_per_hour']:>6} MB/hr  CPU {r['cpu_seconds_per_hour']:>5} s/hr  SDR {r['sdr_tones_db']} dB", flush=True)
    return res

# ---- live cost ----------------------------------------------------------------------------------------------
def child(mode, preset, seconds, out):
    """One recording of the sample in real time, audio off or on (run as its own process so it can be measured)."""
    fk = measure.load_framekeep(measure.ROOT)
    from framekeep import audio, engine
    cfg = fk.config.validate(dict(fk.config.DEFAULT_CONFIG))
    enc = measure.presets(fk)[preset]
    mon = fk.monitors.Monitor(1, r"\\.\DISPLAY1", (0, 0, measure.W, measure.H), (0, 0, measure.W, measure.H - 48), True, None)
    piped = mode != "off"
    cmd = engine.build_record_cmd(measure.FFMPEG, cfg, mon, enc, "gdigrab", out, piped_start=0.0 if piped else None)
    start = cmd.index("gdigrab") - 1
    cmd[start:cmd.index("desktop", start) + 1] = ["-re", "-i", measure.SAMPLE]
    if piped:
        codec = engine.audio_codec(cfg["audio_codec"], enc, "mp4")
        track = audio.AudioTrack(engine.build_audio_cmd(FULL, codec, cfg["quality"]))
        track.start()
        e = engine.Encoder(cmd, out, "measure", engine.build_mux_cmd(measure.FFMPEG, track.path, out, enc), track)
    else:
        e = engine.Encoder(cmd, out, "measure")
    e.start()
    time.sleep(seconds)
    e.stop()

def role(proc, me):
    if proc.pid == me:
        return "app"
    cmd = " ".join(proc.cmdline())
    if " copy " in cmd:
        return "mux"
    if "f32le" in cmd:
        return "audio encoder"
    return "video"

def sample(proc, seconds):
    """CPU % of the PC and mean private MB per role, for proc and its children, over `seconds`."""
    import psutil
    root = psutil.Process(proc.pid)
    cpu0, cpu1, mem, roles = {}, {}, {}, {}
    t0 = time.time()
    while time.time() - t0 < seconds and proc.poll() is None:
        try:
            procs = [root] + root.children(recursive=True)
        except psutil.Error:
            break
        for q in procs:
            try:
                t = q.cpu_times()
                r = roles.setdefault(q.pid, role(q, proc.pid))
                cpu0.setdefault(q.pid, t.user + t.system)
                cpu1[q.pid] = t.user + t.system
                mem.setdefault(r, {}).setdefault(int(time.time() - t0), 0)
                mem[r][int(time.time() - t0)] += q.memory_info().private
            except psutil.Error:
                pass
        time.sleep(1)
    wall = time.time() - t0
    out = {}
    for pid, r in roles.items():
        o = out.setdefault(r, {"cpu_percent": 0.0})
        o["cpu_percent"] += (cpu1[pid] - cpu0[pid]) / wall / os.cpu_count() * 100
    for r, o in out.items():
        o["cpu_percent"] = round(o["cpu_percent"], 3)
        o["private_mb"] = round(statistics.mean(mem[r].values()) / 2**20, 1)
    return out

def play_sound():
    """The fixed clip, looping quietly, so the audio-on runs capture real sound (sizes depend on it)."""
    return subprocess.Popen([FULL_PLAY, "-hide_banner", "-loglevel", "error", "-nodisp", "-loop", "0", *PCM, "-i", SOUND,
                             "-af", "volume=0.1"], stdin=subprocess.DEVNULL)

def run_child(mode, preset, seconds, affinity=None):
    out = os.path.join(OUT, f"live-{preset}-{mode}.mp4")
    if os.path.exists(out):
        os.remove(out)
    args = [sys.executable, os.path.abspath(__file__), "child", mode, preset, str(seconds), out]
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL)
    if affinity:
        import psutil
        psutil.Process(proc.pid).cpu_affinity(affinity)     # inherited by the FFmpeg processes it starts
    time.sleep(3)                                           # skip start-up
    res = sample(proc, seconds - 6)
    proc.wait(60)
    return res, out

def stream_info(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,duration:packet=stream_index,size",
                        "-of", "json", path], capture_output=True, text=True)
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams", [])
    sizes = {}
    for pk in data.get("packets", []):
        sizes[pk["stream_index"]] = sizes.get(pk["stream_index"], 0) + int(pk["size"])
    return {s["codec_type"]: {"codec": s["codec_name"], "duration": float(s.get("duration") or 0),
                              "kbps": round(sizes.get(i, 0) * 8 / max(float(s.get("duration") or 1), 0.1) / 1000, 1)}
            for i, s in enumerate(streams)}

def live(seconds, rounds):
    make_sound()
    player = play_sound()
    runs = {p: {"off": [], "on": []} for p in LIVE_PRESETS}
    try:
        for p in LIVE_PRESETS:
            for r in range(rounds):
                for mode in (("off", "on") if r % 2 == 0 else ("on", "off")):
                    res, out = run_child(mode, p, seconds)
                    res["file"] = stream_info(out)
                    runs[p][mode].append(res)
                    print(f"  {p:<10} audio {mode:<3} round {r + 1}: {json.dumps(res)}", flush=True)
        res, out = run_child("on", "smallest", seconds, affinity=[0, 1])
        res["file"] = stream_info(out)
        print(f"  smallest   audio on, 2 cores: {json.dumps(res)}", flush=True)
        two_cores = res
    finally:
        player.kill()
    summary = {}
    for p, modes in runs.items():
        def med(mode, r, key):
            vals = [x[r][key] for x in modes[mode] if r in x]
            return round(statistics.median(vals), 3) if vals else 0.0
        roles = sorted({r for x in modes["on"] for r in x if r != "file"})
        summary[p] = {mode: {r: {k: med(mode, r, k) for k in ("cpu_percent", "private_mb")} for r in roles}
                      for mode in ("off", "on")}
        summary[p]["audio"] = modes["on"][0]["file"].get("audio")
    return {"seconds": seconds, "rounds": rounds, "summary": summary, "two_cores": two_cores, "runs": runs}

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        return child(sys.argv[2], sys.argv[3], float(sys.argv[4]), sys.argv[5])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", choices=["codecs", "live"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()
    report = {"date": time.strftime("%Y-%m-%d %H:%M"), "cpu_threads": os.cpu_count(),
              "audio_ffmpeg": subprocess.run([FULL, "-hide_banner", "-version"], capture_output=True, text=True).stdout.split("\n")[0]}
    report |= {"results": codecs()} if args.part == "codecs" else live(args.seconds, args.rounds)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

if __name__ == "__main__":
    main()
