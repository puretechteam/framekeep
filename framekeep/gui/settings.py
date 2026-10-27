# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import os, tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from .. import benchmark
from ..engine import CONTAINER_INFO, PRESET_CHAINS, PRESET_INFO, resolve_container, target_size
from ..hotkeys import parse_hotkey
from ..monitors import list_monitors
from .theme import ACCENT, BORDER, GREEN, MUTED, SIDEBAR, YELLOW, Card, font, ghost_button, icon, section_title
from .widgets import PresetPicker, SettingRow, divider, spacer

QUALITY_LABELS = {"high": "High", "balanced": "Balanced", "saver": "Saver", "max_saver": "Max saver"}
SCALES = {"100%": 1.0, "75%": 0.75, "66%": 0.667, "50%": 0.5, "33%": 0.333, "25%": 0.25}
CHUNKS = ["15", "30", "60", "120"]
BACKENDS = {"Auto": "auto", "GPU": "ddagrab", "GDI": "gdigrab"}
PLAYERS = {"Auto": "auto", "Built-in": "builtin", "System": "system"}
CONTAINERS = {CONTAINER_INFO[k][0]: k for k in ("mp4", "mkv", "webm")}

def invert(d):
    return {v: k for k, v in d.items()}

class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, win):
        super().__init__(master, fg_color="transparent")
        self.win, self.app = win, win.app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(26, 10))
        ctk.CTkLabel(head, text="Settings", font=font(26, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(head, text="Changes apply from the next recording file. The current one is finished cleanly first.",
                     font=font(13), text_color=MUTED, anchor="w").pack(anchor="w")
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=(20, 12))
        self._build_footer()
        self.reload()

    # ---- building ----------------------------------------------------------
    def reload(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.cfg = dict(self.app.cfg)
        self.v = {}
        self.monitors = list_monitors()
        self._storage_card()
        self._capture_card()
        self._privacy_card()
        self._general_card()
        for var in self.v.values():
            var.trace_add("write", lambda *_: self._on_change())
        self._on_change()

    def _var(self, key, kind=tk.StringVar, value=None):
        var = kind(value=self.cfg[key] if value is None else value)
        self.v[key] = var
        return var

    def _card(self, title, sub):
        section_title(self.body, title, sub).pack(fill="x", padx=8, pady=(18, 8))
        card = Card(self.body)
        card.pack(fill="x", padx=8)
        spacer(card, 6, top=True)
        return card

    def _switch(self, row, key):
        ctk.CTkSwitch(row.right, text="", variable=self._var(key, tk.BooleanVar), width=46).pack()

    def _segmented(self, row, key, mapping, value=None):
        var = self._var(key, value=value if value is not None else invert(mapping)[self.cfg[key]])
        ctk.CTkSegmentedButton(row.right, values=list(mapping), variable=var, font=font(12)).pack()
        return var

    def _storage_card(self):
        card = self._card("Storage", "Where recordings go and how small they get.")
        row = SettingRow(card, "Recordings folder", "A new folder is created for each day.")
        entry = ctk.CTkEntry(row.right, textvariable=self._var("log_root"), width=320)
        entry.pack(side="left")
        ghost_button(row.right, "Browse", "folder", width=96, command=self._browse_folder).pack(side="left", padx=(8, 0))
        divider(card)
        row = SettingRow(card, "Format", "Smaller formats use newer codecs. The benchmark measures each one on your PC.")
        self.picker = PresetPicker(card, self.app, self._var("preset"), benchmark.load_report())
        self.picker.pack(fill="x", padx=20, pady=(0, 8))
        divider(card)
        row = SettingRow(card, "File type", " ")
        self.container_desc = row.desc
        self._segmented(row, "container", CONTAINERS)
        divider(card)
        row = SettingRow(card, "Quality", "Balanced matches the H.264 default of v2. Saver levels trade detail for space.")
        self._segmented(row, "quality", invert(QUALITY_LABELS))
        divider(card)
        row = SettingRow(card, "Bitrate cap", "Stops fullscreen video and games from ballooning a file. Desktop work is unaffected.")
        self._switch(row, "bitrate_cap")
        spacer(card)

    def _capture_card(self):
        card = self._card("Capture", "What gets recorded and how often.")
        row = SettingRow(card, "Frame rate", "Maximum frames per second. Unchanged frames are skipped anyway.")
        fps = self._var("fps", tk.IntVar)
        self.fps_label = ctk.CTkLabel(row.right, text="", font=font(13, "bold"), width=60)
        self.fps_label.pack(side="right")
        ctk.CTkSlider(row.right, from_=1, to=30, number_of_steps=29, variable=fps, width=220).pack(side="right", padx=8)
        divider(card)
        scale_key = min(SCALES, key=lambda k: abs(SCALES[k] - self.cfg["downscale_factor"]))
        row = SettingRow(card, "Resolution", "Downscale before encoding.")
        self.res_desc = row.desc
        self._segmented(row, "downscale_factor", SCALES, value=scale_key)
        divider(card)
        chunks = CHUNKS if str(self.cfg["chunk_minutes"]) in CHUNKS else sorted(CHUNKS + [str(self.cfg["chunk_minutes"])], key=int)
        row = SettingRow(card, "New file every", "Minutes per recording file.")
        var = self._var("chunk_minutes", value=str(self.cfg["chunk_minutes"]))
        ctk.CTkSegmentedButton(row.right, values=chunks, variable=var, font=font(12)).pack()
        divider(card)
        row = SettingRow(card, "Displays", "Each display records to its own file.")
        self.mon_vars = {}
        all_var = tk.BooleanVar(value="all" in self.cfg["monitors"])
        self.v["_all_monitors"] = all_var
        box = ctk.CTkFrame(row.right, fg_color="transparent")
        box.pack()
        for m in self.monitors:
            on = "all" in self.cfg["monitors"] or m.device in self.cfg["monitors"] or ("primary" in self.cfg["monitors"] and m.primary)
            var = tk.BooleanVar(value=on)
            self.mon_vars[m.device] = var
            self.v[f"_mon_{m.device}"] = var
            ctk.CTkCheckBox(box, text=m.label, variable=var, font=font(12)).pack(anchor="w", pady=2)
        if len(self.monitors) > 1:
            ctk.CTkCheckBox(box, text="All displays, including ones connected later", variable=all_var, font=font(12)).pack(anchor="w", pady=2)
        divider(card)
        row = SettingRow(card, "Capture method", "GPU capture (Desktop Duplication) is lightest. GDI works everywhere.")
        self._segmented(row, "capture_backend", BACKENDS)
        for key, title, desc in (
            ("drop_duplicates", "Skip unchanged frames", "A still screen costs almost nothing."),
            ("draw_mouse", "Show mouse cursor", "Turn off to save frames when only the mouse moves."),
            ("crop_taskbar", "Crop out the taskbar", "Fewer pixels, and the clock stops creating new frames."),
            ("grayscale", "Grayscale", "Drops color for the smallest possible text-only archive."),
        ):
            divider(card)
            self._switch(SettingRow(card, title, desc), key)
        spacer(card)

    def _privacy_card(self):
        card = self._card("Privacy & auto-pause", "Pausing also saves space. Each pause ends the current file; resuming starts a new one.")
        self._switch(SettingRow(card, "Pause when the screen is locked"), "pause_on_lock")
        divider(card)
        row = SettingRow(card, "Pause when you're away", "No keyboard or mouse input for this long.")
        idle = self._var("idle_pause_minutes", tk.IntVar)
        self.idle_label = ctk.CTkLabel(row.right, text="", font=font(13, "bold"), width=60)
        self.idle_label.pack(side="right")
        ctk.CTkSlider(row.right, from_=0, to=60, number_of_steps=60, variable=idle, width=220).pack(side="right", padx=8)
        divider(card)
        row = SettingRow(card, "Private apps", "Recording pauses while one of these is the active window. One program name per line.")
        self.private_box = ctk.CTkTextbox(card, height=96, font=font(12), border_width=1)
        self.private_box.insert("1.0", "\n".join(self.cfg["private_apps"]))
        self.private_box.pack(fill="x", padx=20, pady=(0, 14))
        self.private_box.bind("<KeyRelease>", lambda _e: self._on_change())

    def _general_card(self):
        card = self._card("General", None)
        self._switch(SettingRow(card, "Start recording when FrameKeep opens"), "record_on_launch")
        divider(card)
        self._switch(SettingRow(card, "Start with Windows", "Opens hidden in the tray when you sign in."), "start_with_windows")
        divider(card)
        for key, title in (("hotkey_toggle", "Start / stop hotkey"), ("hotkey_pause", "Pause / resume hotkey")):
            row = SettingRow(card, title, "Works anywhere in Windows, e.g. ctrl+alt+f9.")
            ctk.CTkEntry(row.right, textvariable=self._var(key), width=180).pack()
            divider(card)
        row = SettingRow(card, "Player", "Auto opens H.265/H.266 in FrameKeep's built-in player (arrows seek, space pauses) "
                                         "and everything else in your default app.")
        self._segmented(row, "player", PLAYERS)
        divider(card)
        ff = self.app.recorder.ff
        row = SettingRow(card, "FFmpeg", f"Using {ff.ffmpeg}" if ff.ffmpeg else "Not found. Run dependencies.bat")
        ctk.CTkEntry(row.right, textvariable=self._var("ffmpeg_path"), width=260, placeholder_text="Automatic").pack(side="left")
        ghost_button(row.right, "Browse", "folder", width=96, command=self._browse_ffmpeg).pack(side="left", padx=(8, 0))
        spacer(card)
        ctk.CTkFrame(self.body, height=24, fg_color="transparent").pack()

    def _build_footer(self):
        bar = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, height=64, border_width=0)
        bar.grid(row=2, column=0, sticky="ew")
        ctk.CTkFrame(bar, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x", side="top")
        self.footer_msg = ctk.CTkLabel(bar, text="", font=font(13), text_color=MUTED)
        self.footer_msg.pack(side="left", padx=28, pady=14)
        self.save_btn = ctk.CTkButton(bar, text="Save changes", image=icon("save", 16, "#FFFFFF"), compound="left",
                                      width=150, height=38, font=font(13, "bold"), command=self.save)
        self.save_btn.pack(side="right", padx=(8, 28), pady=12)
        self.revert_btn = ghost_button(bar, "Revert", width=96, height=38, command=self.reload)
        self.revert_btn.pack(side="right", pady=12)

    # ---- behavior ----------------------------------------------------------
    def _browse_folder(self):
        path = filedialog.askdirectory(title="Choose where FrameKeep saves recordings", initialdir=self.v["log_root"].get() or None)
        if path:
            self.v["log_root"].set(path)

    def _browse_ffmpeg(self):
        path = filedialog.askopenfilename(title="Choose ffmpeg.exe", filetypes=[("ffmpeg", "ffmpeg.exe"), ("Programs", "*.exe")])
        if path:
            self.v["ffmpeg_path"].set(path)

    def collect(self):
        cfg = dict(self.app.cfg)
        for key in ("log_root", "preset", "hotkey_toggle", "hotkey_pause", "ffmpeg_path"):
            cfg[key] = self.v[key].get().strip()
        for key in ("bitrate_cap", "drop_duplicates", "draw_mouse", "crop_taskbar", "grayscale", "pause_on_lock",
                    "record_on_launch", "start_with_windows"):
            cfg[key] = bool(self.v[key].get())
        cfg["quality"] = invert(QUALITY_LABELS)[self.v["quality"].get()] if self.v["quality"].get() in QUALITY_LABELS.values() else self.v["quality"].get()
        cfg["fps"] = int(self.v["fps"].get())
        cfg["idle_pause_minutes"] = int(self.v["idle_pause_minutes"].get())
        cfg["downscale_factor"] = SCALES[self.v["downscale_factor"].get()]
        cfg["chunk_minutes"] = int(self.v["chunk_minutes"].get())
        cfg["capture_backend"] = BACKENDS[self.v["capture_backend"].get()]
        cfg["player"] = PLAYERS[self.v["player"].get()]
        cfg["container"] = CONTAINERS[self.v["container"].get()]
        if self.v["_all_monitors"].get():
            cfg["monitors"] = ["all"]
        else:
            chosen = [d for d, var in self.mon_vars.items() if var.get()]
            primary = next((m.device for m in self.monitors if m.primary), None)
            cfg["monitors"] = ["primary"] if chosen in ([primary], []) else chosen
        cfg["private_apps"] = [line.strip().lower() for line in self.private_box.get("1.0", "end").splitlines() if line.strip()]
        return cfg

    def _problems(self, cfg):
        issues = []
        if cfg["log_root"] and not os.path.isdir(cfg["log_root"]):
            issues.append("Recordings folder doesn't exist")
        for key in ("hotkey_toggle", "hotkey_pause"):
            if cfg[key]:
                try:
                    parse_hotkey(cfg[key])
                except ValueError as e:
                    issues.append(f"Hotkey: {e}")
        return issues

    def _on_change(self):
        if not hasattr(self, "save_btn") or "fps" not in self.v:
            return
        try:
            cfg = self.collect()
        except (KeyError, ValueError, tk.TclError):
            return
        self.fps_label.configure(text=f"{cfg['fps']} FPS")
        self.idle_label.configure(text=f"{cfg['idle_pause_minutes']} min" if cfg["idle_pause_minutes"] else "Never")
        if self.monitors:
            m = next((m for m in self.monitors if m.primary), self.monitors[0])
            w, h = target_size(m.rect[2], m.rect[3], cfg["downscale_factor"])
            self.res_desc.configure(text=f"Downscale before encoding. Display {m.index}: {m.rect[2]}×{m.rect[3]} → {w}×{h}")
        encoder = ((self.picker.available or {}).get(cfg["preset"]) or PRESET_CHAINS[cfg["preset"]][0])
        used = resolve_container(cfg["container"], encoder, self.app.recorder.ff.muxers())
        desc = CONTAINER_INFO[cfg["container"]][1]
        if used != cfg["container"]:
            name, codec, _ = PRESET_INFO[cfg["preset"]]
            desc += f" With {name} ({codec}) files are saved as {CONTAINER_INFO[used][0]}."
        self.container_desc.configure(text=desc)
        problems = self._problems(cfg)
        dirty = cfg != self.app.cfg
        if problems:
            self.footer_msg.configure(text="  ·  ".join(problems), text_color=YELLOW)
        else:
            self.footer_msg.configure(text="Unsaved changes" if dirty else "All changes saved", text_color=ACCENT if dirty else MUTED)
        self.save_btn.configure(state="normal" if dirty and not problems else "disabled")
        self.revert_btn.configure(state="normal" if dirty else "disabled")

    def save(self):
        cfg = self.collect()
        if self._problems(cfg):
            return
        self.app.save_settings(cfg)
        self.footer_msg.configure(text="Saved. Applies to the next recording file", text_color=GREEN)
        self.save_btn.configure(state="disabled")
        self.revert_btn.configure(state="disabled")

    def on_show(self):
        self.picker.set_report(benchmark.load_report())
