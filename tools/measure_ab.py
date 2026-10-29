# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License
"""Interleaved A/B of two FrameKeep versions' live recording command (dev tool, needs `pip install psutil`).

  python tools/measure_ab.py --a build/perf/v3.0.0 --b . --rounds 3 --out docs/perf/ab-live.json

At well under 1% CPU, the clock speed Windows picks drifts between runs taken minutes apart, which can swing CPU
time by tens of percent. Alternating A, B, A, B... per format and comparing the medians cancels that drift out.
Uses the sample from `python tools/measure.py sample` and the bundled FFmpeg; writes only under build/perf.
"""

import argparse, json, os, statistics, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure

def commands(repo, out_dir):
    """{preset: live command} for a checkout, built by that checkout's own code in a separate process."""
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import measure; "
            "fk = measure.load_framekeep(sys.argv[2]); cfg = fk.config.validate(dict(fk.config.DEFAULT_CONFIG)); "
            "print(json.dumps({p: measure.recording_cmd(fk, cfg, e, sys.argv[3] + '/' + p + '.mp4', True) "
            "for p, e in measure.presets(fk).items()}))")
    out = subprocess.run([sys.executable, "-c", code, os.path.dirname(os.path.abspath(__file__)), repo, out_dir],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--presets", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out_dir = os.path.join(measure.PERF, "ab")
    os.makedirs(out_dir, exist_ok=True)
    cmds = {"a": commands(args.a, out_dir), "b": commands(args.b, out_dir)}
    presets = [p for p in cmds["b"] if not args.presets or p in args.presets.split(",")]
    runs = {p: {"a": [], "b": []} for p in presets}
    for p in presets:
        for r in range(args.rounds):
            for side in (("a", "b") if r % 2 == 0 else ("b", "a")):   # ABBA order cancels slow drift
                out = os.path.join(out_dir, f"{p}.mp4")
                if os.path.exists(out):
                    os.remove(out)
                proc = subprocess.Popen(cmds[side][p], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=measure.NO_WINDOW)
                res = measure.watch(proc, measure.SECONDS + 30)
                proc.wait()
                runs[p][side].append(res)
                print(f"  {p:<10} {side} round {r + 1}: {res}", flush=True)
                time.sleep(2)
    summary = {}
    for p, sides in runs.items():
        summary[p] = {side: {k: round(statistics.median(x[k] for x in sides[side]), 2)
                             for k in ("cpu_percent", "private_mb_mean", "working_set_mb_mean")} for side in ("a", "b")}
    rel = lambda path: os.path.relpath(os.path.abspath(path), measure.ROOT).replace("\\", "/")
    report = {"a": rel(args.a), "b": rel(args.b), "rounds": args.rounds, "date": time.strftime("%Y-%m-%d %H:%M"),
              "summary": summary, "runs": runs}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    for p, s in summary.items():
        a, b = s["a"], s["b"]
        print(f"{p:<10} CPU {a['cpu_percent']:.2f}% -> {b['cpu_percent']:.2f}%   RAM {a['private_mb_mean']:.0f} -> {b['private_mb_mean']:.0f} MB")

if __name__ == "__main__":
    main()
