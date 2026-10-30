# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import argparse, ctypes, sys
from . import APP_NAME, __version__

def main():
    # Pillow imports NumPy whenever it's installed (only for type hints), and NumPy's math library then commits
    # ~30 MB per CPU thread up front: about 0.5 GB on a 16-thread PC, for a module FrameKeep never uses.
    sys.modules.setdefault("numpy", None)
    parser = argparse.ArgumentParser(prog="framekeep", description="Low-power, low-storage screen and camera recorder")
    parser.add_argument("--version", action="version", version=f"FrameKeep {__version__}")
    parser.add_argument("--no-gui", action="store_true", help="tray icon and console only, like v2")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    parser.add_argument("--benchmark", nargs="?", const=30, type=int, metavar="SECONDS",
                        help="measure every preset on your screen and exit")
    parser.add_argument("--list-monitors", action="store_true", help="show detected monitors and exit")
    args = parser.parse_args()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels for capture rectangles
    except (AttributeError, OSError):
        pass
    if sys.stdout:
        ctypes.windll.kernel32.SetConsoleTitleW(APP_NAME)

    if args.list_monitors:
        from .monitors import list_monitors
        for m in list_monitors():
            print(f"{m.label}  {m.device}  rect={m.rect}  work={m.work}  ddagrab_output={m.dxgi_output}")
        return 0
    if args.benchmark:
        from . import benchmark
        from .config import DEFAULT_CONFIG, load_config, validate
        from .engine import PRESET_INFO
        report = benchmark.run(load_config() or validate(dict(DEFAULT_CONFIG)), seconds=args.benchmark,
                               progress=lambda text, frac: print(f"[{frac:4.0%}] {text}"))
        print(f"\n{'Preset':<12}{'Codec':<22}{'MB/hr':>9}{'CPU %':>8}{'RAM MB':>8}{'VMAF':>7}{'x realtime':>12}")
        for preset, r in report["results"].items():
            name, codec, _ = PRESET_INFO[preset]
            print(f"{name:<12}{codec:<22}{r['mb_per_hour']:>9}{r['cpu_percent']:>8}{str(r.get('ram_mb')):>8}{str(r['vmaf']):>7}{r['realtime']:>12}")
        print(f"\nRecommended: {PRESET_INFO[report['recommended']][0]}")
        return 0

    from .app import App, claim_single_instance
    if not claim_single_instance():
        return 0
    App(gui=not args.no_gui, minimized=args.minimized).run()
    return 0

if __name__ == "__main__":
    sys.exit(main())
