# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import os, threading, tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from .. import __version__, benchmark
from ..engine import PRESET_INFO
from .benchmark_page import ResultsTable
from .theme import ACCENT, BG, GREEN, MUTED, SUBTLE, TEXT, YELLOW, Card, font, ghost_button, icon, set_progress
from .widgets import PresetPicker

STEPS = ("Welcome", "Folder", "Benchmark", "Format")

class Wizard(ctk.CTkFrame):
    def __init__(self, master, app, on_done):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.app, self.on_done = app, on_done
        self.step = 0
        self.report = benchmark.load_report()
        self.folder = tk.StringVar(value=app.cfg["log_root"])
        self.preset = tk.StringVar(value=(self.report or {}).get("recommended") or app.cfg["preset"])
        self.record_now = tk.BooleanVar(value=True)
        self.record_on_launch = tk.BooleanVar(value=True)
        self.start_with_windows = tk.BooleanVar(value=app.cfg["start_with_windows"])
        self.upgrading = bool(app.cfg["log_root"])

        self.panel = Card(self, width=760, corner_radius=18)
        self.panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.7, relheight=0.86)
        self.dots = ctk.CTkFrame(self.panel, fg_color="transparent")
        self.dots.pack(pady=(22, 0))
        # scrollable so every step fits a non-maximized window; the nav buttons stay pinned below it
        self.body = ctk.CTkScrollableFrame(self.panel, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=(40, 24), pady=10)
        nav = ctk.CTkFrame(self.panel, fg_color="transparent")
        nav.pack(fill="x", padx=40, pady=(0, 26))
        self.back_btn = ghost_button(nav, "Back", width=100, command=lambda: self.go(self.step - 1))
        self.back_btn.pack(side="left")
        self.next_btn = ctk.CTkButton(nav, text="Next", width=150, height=40, font=font(14, "bold"), command=self.next)
        self.next_btn.pack(side="right")
        self.folder.trace_add("write", lambda *_: self._validate())
        self.go(0)

    def go(self, step):
        self.step = max(0, min(step, len(STEPS) - 1))
        for w in self.body.winfo_children():
            w.destroy()
        self.body._parent_canvas.yview_moveto(0)  # each step starts at the top
        for w in self.dots.winfo_children():
            w.destroy()
        for i, name in enumerate(STEPS):
            color = ACCENT if i == self.step else (TEXT if i < self.step else SUBTLE)
            ctk.CTkLabel(self.dots, text=f"{i + 1}. {name}", font=font(12, "bold" if i == self.step else "normal"),
                         text_color=color).pack(side="left", padx=10)
        [self._welcome, self._folder, self._benchmark, self._format][self.step]()
        self.back_btn.configure(state="normal" if self.step else "disabled")
        self.next_btn.configure(text="Finish" if self.step == len(STEPS) - 1 else "Next")
        self._validate()

    def _validate(self):
        ok = not (self.step == 1 and not os.path.isdir(self.folder.get()))
        self.next_btn.configure(state="normal" if ok else "disabled")

    def next(self):
        if self.step == len(STEPS) - 1:
            self.finish()
        else:
            self.go(self.step + 1)

    def _heading(self, title, sub):
        ctk.CTkLabel(self.body, text=title, font=font(28, "bold", display=True), anchor="w").pack(anchor="w", pady=(16, 4))
        ctk.CTkLabel(self.body, text=sub, font=font(14), text_color=MUTED, anchor="w", justify="left", wraplength=640).pack(anchor="w", pady=(0, 18))

    # ---- steps -------------------------------------------------------------
    def _welcome(self):
        ctk.CTkLabel(self.body, text="", image=icon("record", 64, ACCENT)).pack(pady=(30, 10))
        ctk.CTkLabel(self.body, text=f"FrameKeep {__version__}", font=font(34, "bold", display=True)).pack()
        ctk.CTkLabel(self.body, text="Welcome back! Your settings came along." if self.upgrading else
                     "A low-power screen recorder for all the moments you wish you had saved.",
                     font=font(15), text_color=MUTED).pack(pady=(6, 26))
        for ic, title, sub in (
            ("leaf", "Much smaller recordings", "H.266 and AV1 screen-content encoding, and unchanged frames are skipped."),
            ("shield", "Never touches your files", "FrameKeep only creates new recordings. It never edits, moves or deletes them. That's up to you."),
            ("privacy", "Pauses on its own", "When you lock the screen, step away, or open a private app."),
        ):
            row = ctk.CTkFrame(self.body, fg_color="transparent")
            row.pack(fill="x", pady=7, padx=40)
            ctk.CTkLabel(row, text="", image=icon(ic, 22, ACCENT)).pack(side="left", padx=(0, 14))
            txt = ctk.CTkFrame(row, fg_color="transparent")
            txt.pack(side="left", fill="x")
            ctk.CTkLabel(txt, text=title, font=font(14, "bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(txt, text=sub, font=font(12), text_color=MUTED, anchor="w").pack(anchor="w")

    def _folder(self):
        self._heading("Where should recordings go?", "FrameKeep makes a folder for each day inside this one. Pick a drive with room to spare.")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkEntry(row, textvariable=self.folder, height=40, font=font(13)).pack(side="left", fill="x", expand=True)
        ghost_button(row, "Browse", "folder", width=110, height=40, command=self._browse).pack(side="left", padx=(10, 0))

    def _browse(self):
        path = filedialog.askdirectory(title="Choose where FrameKeep saves recordings", initialdir=self.folder.get() or None)
        if path:
            self.folder.set(path)

    def _benchmark(self):
        self._heading("Find your smallest format", "A quick test records your screen for 30 seconds, then tries every format on that "
                                                   "sample. Use your PC normally while it runs. You can skip this and run it later.")
        self.bench_btn = ctk.CTkButton(self.body, text="Run benchmark", image=icon("play", 14, "#FFFFFF"), compound="left",
                                       width=190, height=40, font=font(14, "bold"), command=self._run_bench)
        self.bench_btn.pack(anchor="w")
        self.bench_bar = ctk.CTkProgressBar(self.body, height=6)
        set_progress(self.bench_bar, 1 if self.report else 0)
        self.bench_bar.pack(fill="x", pady=(14, 4))
        self.bench_label = ctk.CTkLabel(self.body, text="", font=font(12), text_color=MUTED, anchor="w")
        self.bench_label.pack(fill="x")
        self.bench_table = ResultsTable(self.body, compact=True)
        self.bench_table.pack(fill="x", pady=10)
        if self.report:
            self.bench_table.show(self.report)
            self.bench_btn.configure(text="Run again")

    def _run_bench(self):
        self.bench_btn.configure(state="disabled", text="Running…")
        self.next_btn.configure(state="disabled")
        cfg = self.app.cfg | {"log_root": self.folder.get()}
        def progress(text, frac):
            self.app.ui_queue.put(lambda: self.winfo_exists() and self.step == 2 and (set_progress(self.bench_bar, frac), self.bench_label.configure(text=text)))
        def work():
            try:
                report, err = benchmark.run(cfg, 30, progress), None
            except Exception as e:
                report, err = None, str(e)
            self.app.ui_queue.put(lambda: self._bench_done(report, err))
        threading.Thread(target=work, daemon=True).start()

    def _bench_done(self, report, err):
        if report:
            self.report = report
            self.preset.set(report["recommended"])
        if self.step != 2:
            return
        self.next_btn.configure(state="normal")
        self.bench_btn.configure(state="normal", text="Run again")
        if err:
            self.bench_label.configure(text=f"Benchmark failed: {err}", text_color=YELLOW)
        else:
            self.bench_label.configure(text=f"Recommended: {PRESET_INFO[report['recommended']][0]}", text_color=GREEN)
            self.bench_table.show(report)

    def _format(self):
        self._heading("Pick a format", "You can change this any time in Settings.")
        PresetPicker(self.body, self.app, self.preset, self.report).pack(fill="x")
        ctk.CTkLabel(self.body, text="Ultra (H.266) files open in FrameKeep's built-in player. For other apps, use "
                                  "“Share copy” on the Recordings page. It makes a new H.264 file and leaves the original alone.",
                     font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=600).pack(fill="x", pady=(8, 12))
        opts = ctk.CTkFrame(self.body, fg_color="transparent")
        opts.pack(fill="x", pady=(8, 0))
        ctk.CTkCheckBox(opts, text="Start recording now", variable=self.record_now, font=font(13)).pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(opts, text="Record whenever FrameKeep opens", variable=self.record_on_launch, font=font(13)).pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(opts, text="Start with Windows", variable=self.start_with_windows, font=font(13)).pack(side="left")

    def finish(self):
        self.app.save_settings(self.app.cfg | {"log_root": self.folder.get(), "preset": self.preset.get(), "onboarded": True,
                                               "record_on_launch": self.record_on_launch.get(),
                                               "start_with_windows": self.start_with_windows.get()})
        self.app.first_run = False
        if self.record_now.get():
            self.app.recorder.start()
        self.on_done()
