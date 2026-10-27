# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import datetime, os, threading, time
import customtkinter as ctk
from .. import benchmark, stats
from ..engine import PRESET_INFO
from .theme import (ACCENT, ACCENT_HOVER, BLUE, BORDER, CARD_HI, GREEN, MUTED, PURPLE, REASON_TEXT, STATE_STYLE, SUBTLE,
                    TEXT, YELLOW, Badge, Card, Pill, StatTile, font, ghost_button, icon, set_progress, shorten)

class DashboardPage(ctk.CTkScrollableFrame):
    def __init__(self, master, win):
        super().__init__(master, fg_color="transparent")
        self.win, self.app = win, win.app
        self.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="col")
        self._summary = None
        self._summary_at = 0
        self._summary_files = None
        self._report = benchmark.load_report()

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=4, sticky="ew", padx=28, pady=(26, 14))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Dashboard", font=font(26, "bold", display=True), anchor="w").pack(anchor="w")
        self.date_label = ctk.CTkLabel(titles, text="", font=font(13), text_color=MUTED, anchor="w")
        self.date_label.pack(anchor="w")
        self.pill = Pill(head, "Ready", MUTED)
        self.pill.pack(side="right")

        # banner for errors / nudges, gridded only when needed
        self.banner = Card(self, fg_color="#1F1A12", border_color="#4A3A16")
        self.banner_icon = ctk.CTkLabel(self.banner, text="", image=icon("warning", 18, YELLOW))
        self.banner_icon.pack(side="left", padx=(16, 8), pady=14)
        self.banner_text = ctk.CTkLabel(self.banner, text="", font=font(13), anchor="w", justify="left", wraplength=640)
        self.banner_text.pack(side="left", fill="x", expand=True, pady=14)
        self.banner_button = ghost_button(self.banner, "", height=32)
        self.banner_button.pack(side="right", padx=14)

        self._build_hero()
        self.tiles = {
            "chunk": StatTile(self, "This chunk", "video", ACCENT),
            "today": StatTile(self, "Today", "calendar", BLUE),
            "library": StatTile(self, "Library", "drive", GREEN),
            "rate": StatTile(self, "Per active hour", "leaf", PURPLE),
        }
        for i, tile in enumerate(self.tiles.values()):
            tile.grid(row=3, column=i, sticky="nsew", padx=(28 if i == 0 else 7, 28 if i == 3 else 7), pady=7)
        self._build_info_cards()

    # ---- construction ------------------------------------------------------
    def _build_hero(self):
        hero = Card(self)
        hero.grid(row=2, column=0, columnspan=4, sticky="ew", padx=28, pady=(0, 7))
        hero.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(hero, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=24, pady=22)
        self.hero_state = ctk.CTkLabel(left, text="Ready to record", font=font(15, "bold"), text_color=MUTED, anchor="w")
        self.hero_state.pack(anchor="w")
        self.hero_timer = ctk.CTkLabel(left, text="00:00:00", font=font(52, "bold", display=True), anchor="w")
        self.hero_timer.pack(anchor="w", pady=(2, 6))
        self.hero_progress = ctk.CTkProgressBar(left, height=6, corner_radius=3)
        set_progress(self.hero_progress, 0)
        self.hero_progress.pack(fill="x", pady=(0, 8))
        self.hero_file = ctk.CTkLabel(left, text="", font=font(12), text_color=MUTED, anchor="w")
        self.hero_file.pack(anchor="w")

        right = ctk.CTkFrame(hero, fg_color="transparent")
        right.grid(row=0, column=1, sticky="ns", padx=24, pady=22)
        self.toggle_btn = ctk.CTkButton(right, text="Start recording", image=icon("record", 18, "#FFFFFF"), compound="left",
                                        width=220, height=48, font=font(15, "bold"), command=self.app.recorder.toggle)
        self.toggle_btn.pack(fill="x")
        self.pause_btn = ghost_button(right, "Pause", "pause", command=self.app.recorder.toggle_pause, height=40)
        self.pause_btn.pack(fill="x", pady=(10, 0))
        ghost_button(right, "Open recordings folder", "open_folder", height=40,
                     command=self._open_folder).pack(fill="x", pady=(10, 0))

    def _build_info_cards(self):
        enc = Card(self)
        enc.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=(28, 7), pady=(7, 28))
        ctk.CTkLabel(enc, text="Encoder", font=font(15, "bold", display=True), anchor="w").pack(fill="x", padx=20, pady=(16, 8))
        self.enc_rows = {k: self._kv(enc, k) for k in ("Format", "Encoder", "Capture", "Output", "Quality")}

        auto = Card(self)
        auto.grid(row=4, column=2, columnspan=2, sticky="nsew", padx=(7, 28), pady=(7, 28))
        ctk.CTkLabel(auto, text="Auto-pause", font=font(15, "bold", display=True), anchor="w").pack(fill="x", padx=20, pady=(16, 8))
        self.auto_rows = {}
        for key, ic, label in (("locked", "lock", "When the screen is locked"), ("idle", "idle", "When you're away"),
                               ("private", "privacy", "When a private app is in focus")):
            row = ctk.CTkFrame(auto, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=5)
            ctk.CTkLabel(row, text="", image=icon(ic, 16, MUTED)).pack(side="left")
            ctk.CTkLabel(row, text=label, font=font(13), anchor="w").pack(side="left", padx=10)
            value = ctk.CTkLabel(row, text="", font=font(13, "bold"), text_color=MUTED)
            value.pack(side="right")
            self.auto_rows[key] = value
        ctk.CTkFrame(auto, height=10, width=1, fg_color="transparent").pack(pady=(0, 3))

    def _kv(self, parent, key):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(row, text=key, font=font(13), text_color=MUTED, width=90, anchor="w").pack(side="left")
        value = ctk.CTkLabel(row, text="-", font=font(13), anchor="w")
        value.pack(side="left", fill="x", expand=True)
        if key == "Format":
            badge = Badge(row, "", ACCENT)
            badge.pack(side="right")
            value.badge = badge
        return value

    # ---- updates -----------------------------------------------------------
    def on_show(self):
        self._report = benchmark.load_report()
        self._summary_files = None  # rescan right away

    def reload(self):
        self.on_show()

    def _open_folder(self):
        root = self.app.cfg["log_root"]
        if root and os.path.isdir(root):
            os.startfile(root)

    def _refresh_summary(self, active):
        """Rescan finished recordings in the background. The live file's size is added on every tick instead."""
        root = self.app.cfg["log_root"]
        def work():
            self._summary = stats.library_summary(root, exclude=active)
        threading.Thread(target=work, daemon=True).start()
        self._summary_at = time.time()
        self._summary_files = tuple(active)

    def on_status(self, s):
        cfg = self.app.cfg
        self.date_label.configure(text=datetime.datetime.now().strftime("%A, %B %d").replace(" 0", " "))
        label, color = STATE_STYLE[s["state"]]
        reason = REASON_TEXT.get(s["reason"]) if s["state"] == "auto_paused" else None
        self.pill.set(f"{label}: {reason}" if reason else label, color)

        # hero
        active = s["state"] != "idle"
        if s["state"] == "recording" and s["chunk_start"]:
            elapsed = time.time() - s["chunk_start"]
            self.hero_timer.configure(text=time.strftime("%H:%M:%S", time.gmtime(elapsed)), text_color=TEXT)
            set_progress(self.hero_progress, min(1, elapsed / s["chunk_seconds"]))
            self.hero_state.configure(text=f"Recording  ·  new file every {cfg['chunk_minutes']} min", text_color=ACCENT)
            self.hero_file.configure(text=" · ".join(os.path.basename(f) for f in s["files"]) + f"   in   {shorten(os.path.dirname(s['files'][0]), 60)}" if s["files"] else "")
        else:
            set_progress(self.hero_progress, 0)
            self.hero_timer.configure(text="00:00:00", text_color=SUBTLE)
            text = {"paused": "Paused. Resuming starts a new file.", "starting": "Starting…",
                    "auto_paused": f"Auto-paused because {reason}. Resumes automatically.",
                    "idle": "Ready to record"}[s["state"]]
            self.hero_state.configure(text=text, text_color=color if active else MUTED)
            self.hero_file.configure(text=f"Saving to {shorten(cfg['log_root'])}" if cfg["log_root"] else "No recordings folder chosen yet")
        self.toggle_btn.configure(text="Stop recording" if active else "Start recording",
                                  image=icon("stop" if active else "record", 18, "#FFFFFF"),
                                  fg_color=CARD_HI if active else ACCENT, hover_color=BORDER if active else ACCENT_HOVER,
                                  border_width=1 if active else 0, border_color=BORDER)
        paused = s["state"] == "paused"
        self.pause_btn.configure(text="Resume" if paused else "Pause", image=icon("play" if paused else "pause", 16),
                                 state="normal" if active else "disabled")

        # tiles: finished files come from a background scan (redone when a new file starts), the live file every tick
        if tuple(s["files"]) != self._summary_files or time.time() - self._summary_at > 30:
            self._refresh_summary(s["files"])
        live = 0
        for f in s["files"]:
            try:
                live += os.path.getsize(f)
            except OSError:
                pass
        n_live = len(s["files"])
        if n_live:
            self.tiles["chunk"].set(stats.human_bytes(live), f"{stats.plural(n_live, 'file')} being written")
        else:
            self.tiles["chunk"].set("-", "Not recording")
        if self._summary:
            sm = self._summary
            self.tiles["today"].set(stats.human_bytes(sm["today"] + live), f"{stats.plural(sm['today_files'] + n_live, 'recording')} today")
            self.tiles["library"].set(stats.human_bytes(sm["total"] + live),
                                      f"{stats.plural(sm['files'] + n_live, 'recording')} over {stats.plural(sm['days'], 'day')}")
        rate = (self._report or {}).get("results", {}).get(s["preset"] or cfg["preset"])
        if rate:
            mb = rate["mb_per_hour"]
            self.tiles["rate"].set(f"{mb:.0f} MB", f"≈ {mb * 8 * 30 / 1024:.1f} GB/month at 8 h/day")
        else:
            self.tiles["rate"].set("-", "Run the benchmark to measure")

        # encoder card
        if s["preset"]:
            name, codec, _ = PRESET_INFO[s["preset"]]
            self.enc_rows["Format"].configure(text=name)
            self.enc_rows["Format"].badge.configure(text=codec)
            self.enc_rows["Encoder"].configure(text=s["encoder"])
            backends = sorted(set(s["backends"].values()))
            self.enc_rows["Capture"].configure(text=", ".join("GPU (Desktop Duplication)" if b == "ddagrab" else "GDI" for b in backends)
                                               + f" · {len(s['monitors'])} display{'s' if len(s['monitors']) != 1 else ''}")
            if s["resolution"]:
                w, h = s["resolution"]
                fps = f"up to {s['fps']} FPS, unchanged frames skipped" if cfg["drop_duplicates"] else f"{s['fps']} FPS"
                self.enc_rows["Output"].configure(text=f"{w}×{h} @ {fps} · .{s['container']}")
            self.enc_rows["Quality"].configure(text=cfg["quality"].replace("_", " ").capitalize() + (" · bitrate cap on" if cfg["bitrate_cap"] else ""))

        # auto-pause card
        def flag(key, on, text):
            hit = s["state"] == "auto_paused" and s["reason"] == key
            self.auto_rows[key].configure(text="Active now" if hit else text, text_color=PURPLE if hit else (TEXT if on else SUBTLE))
        flag("locked", cfg["pause_on_lock"], "On" if cfg["pause_on_lock"] else "Off")
        flag("idle", cfg["idle_pause_minutes"] > 0, f"After {cfg['idle_pause_minutes']} min" if cfg["idle_pause_minutes"] else "Off")
        flag("private", bool(cfg["private_apps"]), f"{len(cfg['private_apps'])} apps" if cfg["private_apps"] else "None set")

        self._update_banner(s)

    def _update_banner(self, s):
        msg = action = None
        if s["error"]:
            msg, action = s["error"], ("View logs", lambda: self.win.show_page("logs"))
        elif not self.app.cfg["log_root"]:
            msg, action = "Choose where recordings are saved to start recording.", ("Open settings", lambda: self.win.show_page("settings"))
        elif not self._report:
            msg, action = ("Run the 30-second benchmark to find the smallest format your PC can record live.",
                           ("Run benchmark", lambda: self.win.show_page("benchmark")))
        if msg:
            self.banner_text.configure(text=msg)
            self.banner_button.configure(text=action[0], command=action[1])
            self.banner.grid(row=1, column=0, columnspan=4, sticky="ew", padx=28, pady=(0, 14))
        else:
            self.banner.grid_remove()
