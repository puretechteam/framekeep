# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import threading
import customtkinter as ctk
from .. import benchmark
from ..engine import PRESET_INFO
from .theme import ACCENT, GREEN, MUTED, PURPLE, SUBTLE, TEXT, YELLOW, Badge, Card, font, icon, set_progress
from .widgets import PRESET_ORDER

class ResultsTable(ctk.CTkFrame):
    """Preset rows with a size bar, CPU, quality and speed. Reused by the wizard."""
    COLS = ("Format", "Size per active hour", "CPU", "Quality", "Speed", "")

    def __init__(self, master, compact=False):
        super().__init__(master, fg_color="transparent")
        self.compact = compact

    def show(self, report):
        for w in self.winfo_children():
            w.destroy()
        if not report:
            return
        results, rec = report["results"], report["recommended"]
        why = benchmark.reasons(results, rec)
        self.grid_columnconfigure(1, weight=1)
        cols = self.COLS if not self.compact else ("Format", "Size per active hour", "CPU", "Quality", "")
        for c, title in enumerate(cols):
            ctk.CTkLabel(self, text=title.upper(), font=font(11, "bold"), text_color=SUBTLE, anchor="w").grid(
                row=0, column=c, sticky="w", padx=10, pady=(0, 6))
        biggest = max(r["mb_per_hour"] for r in results.values()) or 1
        for i, key in enumerate(k for k in PRESET_ORDER if k in results):
            r = results[key]
            name, codec, _ = PRESET_INFO[key]
            row = i + 1
            cell = ctk.CTkFrame(self, fg_color="transparent")
            cell.grid(row=row, column=0, sticky="w", padx=10, pady=7)
            ctk.CTkLabel(cell, text=name, font=font(14, "bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(cell, text=codec, font=font(11), text_color=MUTED, anchor="w").pack(anchor="w")
            bar_cell = ctk.CTkFrame(self, fg_color="transparent")
            bar_cell.grid(row=row, column=1, sticky="ew", padx=10)
            bar = ctk.CTkProgressBar(bar_cell, height=10, corner_radius=5, progress_color=GREEN if key == rec else ACCENT if key == "ultra" else PURPLE)
            bar.set(max(0.02, r["mb_per_hour"] / biggest))
            bar.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(bar_cell, text=f"{r['mb_per_hour']:.0f} MB", font=font(13, "bold"), width=80, anchor="e").pack(side="left", padx=(10, 0))
            issue = benchmark.problem(r)
            ctk.CTkLabel(self, text=f"{r['cpu_percent']:.1f}%", font=font(13),
                         text_color=YELLOW if issue == "heavy" else TEXT).grid(row=row, column=2, padx=10)
            ctk.CTkLabel(self, text=f"{r['vmaf']:.1f}" if r["vmaf"] is not None else "-", font=font(13),
                         text_color=YELLOW if issue == "quality" else TEXT).grid(row=row, column=3, padx=10)
            col = 4
            if not self.compact:
                ctk.CTkLabel(self, text=f"{r['realtime']:.1f}×", font=font(13),
                             text_color=YELLOW if issue == "heavy" else TEXT).grid(row=row, column=4, padx=10)
                col = 5
            badge, _ = why[key]
            color = GREEN if key == rec else YELLOW if issue else MUTED
            Badge(self, badge, color).grid(row=row, column=col, padx=10, sticky="w")
        note = ctk.CTkLabel(self, text=benchmark.explain(report), font=font(12), text_color=MUTED, anchor="w",
                            justify="left", wraplength=640 if self.compact else 820)
        note.grid(row=len(results) + 1, column=0, columnspan=len(cols), sticky="w", padx=10, pady=(10, 0))

class BenchmarkPage(ctk.CTkScrollableFrame):
    def __init__(self, master, win):
        super().__init__(master, fg_color="transparent")
        self.win, self.app = win, win.app
        self.running = False
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(26, 14))
        ctk.CTkLabel(head, text="Benchmark", font=font(26, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(head, text="Find the smallest format your PC can record live, measured on your own screen instead of guessed.",
                     font=font(13), text_color=MUTED, anchor="w").pack(anchor="w")

        run = Card(self)
        run.pack(fill="x", padx=28, pady=7)
        row = ctk.CTkFrame(run, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(row, text="", image=icon("benchmark", 28, ACCENT)).pack(side="left")
        txt = ctk.CTkFrame(row, fg_color="transparent")
        txt.pack(side="left", padx=14, fill="x", expand=True)
        ctk.CTkLabel(txt, text="How it works", font=font(15, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(txt, text="FrameKeep captures a short sample of your screen, then encodes that same sample with every format "
                               "available, using your current quality, FPS and resolution settings. Use your PC the way you "
                               "normally do while it captures (scrolling, typing, switching windows) for realistic numbers.",
                     font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=700).pack(anchor="w")
        controls = ctk.CTkFrame(run, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=(10, 6))
        ctk.CTkLabel(controls, text="Sample length", font=font(13), text_color=MUTED).pack(side="left")
        self.length = ctk.CTkSegmentedButton(controls, values=["15 s", "30 s", "60 s"], font=font(12))
        self.length.set("30 s")
        self.length.pack(side="left", padx=12)
        self.run_btn = ctk.CTkButton(controls, text="Run benchmark", image=icon("play", 14, "#FFFFFF"), compound="left",
                                     width=170, height=38, font=font(13, "bold"), command=self.start)
        self.run_btn.pack(side="right")
        self.progress = ctk.CTkProgressBar(run, height=6)
        set_progress(self.progress, 0)
        self.progress.pack(fill="x", padx=20, pady=(10, 4))
        self.progress_label = ctk.CTkLabel(run, text="", font=font(12), text_color=MUTED, anchor="w")
        self.progress_label.pack(fill="x", padx=20, pady=(0, 16))

        res = Card(self)
        res.pack(fill="x", padx=28, pady=(7, 28))
        top = ctk.CTkFrame(res, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(18, 10))
        ctk.CTkLabel(top, text="Results", font=font(15, "bold", display=True)).pack(side="left")
        self.meta = ctk.CTkLabel(top, text="", font=font(12), text_color=MUTED)
        self.meta.pack(side="left", padx=12)
        self.use_btn = ctk.CTkButton(top, text="Use recommended", width=170, height=34, font=font(13, "bold"), command=self.use_recommended)
        self.use_btn.pack(side="right")
        self.table = ResultsTable(res)
        self.table.pack(fill="x", padx=10, pady=(0, 10))
        self.note = ctk.CTkLabel(res, text="", font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=820)
        self.note.pack(fill="x", padx=20, pady=(0, 18))
        self.show(benchmark.load_report())

    def on_show(self):
        if not self.running:
            self.show(benchmark.load_report())

    def show(self, report):
        self.report = report
        self.table.show(report)
        if not report:
            self.meta.configure(text="No benchmark yet")
            self.use_btn.configure(state="disabled", text="Use recommended")
            self.note.configure(text="")
            return
        rec = report["recommended"]
        self.meta.configure(text=f"{report['date'].replace('T', ' ')} · {report['seconds']} s sample · {report['fps']} FPS · "
                                 f"{int(report['downscale_factor'] * 100)}% · {report['quality'].replace('_', ' ')}")
        current = self.app.cfg["preset"] == rec
        self.use_btn.configure(state="disabled" if current else "normal",
                               text="In use" if current else f"Use {PRESET_INFO[rec][0]}")
        self.note.configure(text="How it's picked: the smallest file that runs comfortably and still looks good. "
                                 f"\"Too heavy\" means over {benchmark.CPU_TARGET:.0f}% CPU or under "
                                 f"{benchmark.REALTIME_MARGIN:.0f}× real time. \"Lower quality\" means a VMAF score under "
                                 f"{benchmark.QUALITY_FLOOR:.0f} (0 to 100; above about 93, most people can't tell it from the "
                                 "original). Size is per hour of screen activity, so a still or locked screen records almost nothing. "
                                 "Fast-moving content like games needs more data and scores lower, so run it while doing what you usually do.")

    def start(self):
        if self.running:
            return
        self.running = True
        self.run_btn.configure(state="disabled", text="Running…")
        seconds = int(self.length.get().split()[0])
        cfg = dict(self.app.cfg)
        def progress(text, frac):
            self.app.ui_queue.put(lambda: (set_progress(self.progress, frac), self.progress_label.configure(text=text)))
        def work():
            try:
                report = benchmark.run(cfg, seconds, progress)
                self.app.ui_queue.put(lambda: self._done(report, None))
            except Exception as e:
                self.app.ui_queue.put(lambda err=str(e): self._done(None, err))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, report, error):
        self.running = False
        self.run_btn.configure(state="normal", text="Run again")
        if error:
            self.progress_label.configure(text=f"Benchmark failed: {error}", text_color=YELLOW)
            return
        self.progress_label.configure(text="Done", text_color=MUTED)
        self.show(report)

    def use_recommended(self):
        if self.report:
            self.app.save_settings(self.app.cfg | {"preset": self.report["recommended"]})
            self.show(self.report)
            settings = self.win.pages.get("settings")
            if settings:
                settings.reload()
