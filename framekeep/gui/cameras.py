# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""The Cameras page: live tiles for every camera, quick pause (to free a webcam for a call), today's motion events,
and the add/edit dialog."""

import datetime, os, threading, time, tkinter as tk
import customtkinter as ctk
from PIL import Image
from .. import cameras, motion, stats
from ..config import CAMERA_DEFAULTS, CAMERA_HEIGHTS, camera_root, validate_camera
from ..engine import ANALYSIS_H, ANALYSIS_W, PRESET_INFO
from .theme import (ACCENT, BORDER, CARD, CARD_HI, GREEN, INPUT, MUTED, PURPLE, SUBTLE, YELLOW, Badge, Card, Pill, font,
                    ghost_button, icon, update)
from .widgets import SettingRow, divider, spacer

TILE_W, TILE_H = ANALYSIS_W * 2, ANALYSIS_H * 2
STATE = {   # camera state -> (label, color)
    "recording": ("Recording", ACCENT), "starting": ("Starting…", ACCENT), "paused": ("Paused", YELLOW),
    "app_paused": ("Paused for an app", PURPLE), "busy": ("In use by another app", YELLOW),
    "offline": ("Not connected", SUBTLE), "error": ("Problem", YELLOW), "off": ("Off", SUBTLE),
}
FPS_CHOICES = ["5", "10", "15", "20", "30"]
HEIGHTS = {f"{h}p": h for h in CAMERA_HEIGHTS}
FORMATS = {"Same as desktop": "desktop", **{f"{PRESET_INFO[k][0]} ({PRESET_INFO[k][1]})": k
                                            for k in ("smallest", "smaller", "compatible", "lowcpu")}}

def invert(d):
    return {v: k for k, v in d.items()}

def to_image(rgb, w=ANALYSIS_W, h=ANALYSIS_H, size=(TILE_W, TILE_H)):
    img = Image.frombytes("RGB", (w, h), rgb)
    return ctk.CTkImage(light_image=img, dark_image=img, size=size)

def placeholder():
    img = Image.new("RGB", (ANALYSIS_W, ANALYSIS_H), "#0B0D11")
    return ctk.CTkImage(light_image=img, dark_image=img, size=(TILE_W, TILE_H))

class CameraTile(Card):
    def __init__(self, master, page, cam):
        super().__init__(master)
        self.page, self.cam_id = page, cam["id"]
        self.shown_at = 0.0
        self.view = ctk.CTkLabel(self, text="", image=placeholder(), width=TILE_W, height=TILE_H, corner_radius=8,
                                 fg_color="#0B0D11", text_color=MUTED, font=font(13))
        self.view.pack(padx=12, pady=(12, 8))
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14)
        self.name = ctk.CTkLabel(top, text=cam["name"], font=font(15, "bold"), anchor="w")
        self.name.pack(side="left")
        self.motion = Badge(top, "Motion", ACCENT)
        self.pill = Pill(top, "Off", SUBTLE)
        self.pill.pack(side="right")
        self.info = ctk.CTkLabel(self, text="", font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=TILE_W)
        self.info.pack(fill="x", padx=14, pady=(4, 6))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        self.pause_btn = ghost_button(buttons, "Pause", "pause", height=32, width=110,
                                      command=lambda: page.app.station.toggle(self.cam_id))
        self.pause_btn.pack(side="left")
        ghost_button(buttons, "Edit", "settings", height=32, width=90,
                     command=lambda: page.edit(self.cam_id)).pack(side="left", padx=6)
        ghost_button(buttons, "Folder", "open_folder", height=32, width=96,
                     command=lambda: page.open_folder(self.cam_id)).pack(side="left")

    def refresh(self, s, rec):
        label, color = STATE.get(s["state"], STATE["off"])
        if s["state"] == "app_paused" and s["app"]:
            label = f"Paused: {s['app']} is open"
        self.pill.set(label, color)
        if s["motion"]:
            self.motion.pack(side="left", padx=8)
        else:
            self.motion.pack_forget()
        paused = rec.user_paused
        update(self.pause_btn, text="Resume" if paused else "Pause", image=icon("play" if paused else "pause", 16))
        parts = []
        if s["state"] == "recording" and s["since"]:
            elapsed = int((datetime.datetime.now() - s["since"]).total_seconds())
            parts.append(f"{elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}")
            parts.append("camera's own stream" if s["copy"] else (s["encoder"] or ""))
            if s["audio"]:
                parts.append(s["audio"].upper() if s["audio"] == "aac" else "Opus")
        elif s["error"] and s["state"] in ("error", "busy", "offline"):
            parts.append(s["error"])
        parts.append(f"{stats.plural(s['events_today'], 'motion event')} today" if rec.cam["motion"] else "Motion detection off")
        update(self.info, text="  ·  ".join(p for p in parts if p))
        frame = rec.frame()
        live = frame and s["state"] in ("recording", "starting") and time.time() - frame[3] < 5
        if live and frame[3] != self.shown_at:
            self.shown_at = frame[3]
            self.view.configure(image=to_image(frame[0]))
            update(self.view, text="")
        elif not live:
            if self.shown_at != -1:
                self.shown_at = -1
                self.view.configure(image=placeholder())
            update(self.view, text=label)

class CamerasPage(ctk.CTkScrollableFrame):
    def __init__(self, master, win):
        super().__init__(master, fg_color="transparent")
        self.win, self.app = win, win.app
        self.grid_columnconfigure((0, 1), weight=1, uniform="col")
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(26, 14))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Cameras", font=font(26, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(titles, text="Webcams, capture cards, phones and network cameras, recording around the clock. "
                                  "They run on their own, separate from desktop recording.",
                     font=font(13), text_color=MUTED, anchor="w", justify="left", wraplength=560).pack(anchor="w")
        ctk.CTkButton(head, text="Add camera", image=icon("video", 16, "#FFFFFF"), compound="left", height=38,
                      font=font(13, "bold"), command=lambda: self.edit(None)).pack(side="right")
        self.pause_all = ghost_button(head, "Pause all", "pause", height=38, command=self._toggle_all)
        self.pause_all.pack(side="right", padx=8)
        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=21)
        self.grid_frame.grid_columnconfigure((0, 1), weight=1, uniform="tile")
        self.empty = Card(self)
        ctk.CTkLabel(self.empty, text="", image=icon("video", 36, SUBTLE)).pack(pady=(28, 6))
        ctk.CTkLabel(self.empty, text="No cameras yet", font=font(16, "bold")).pack()
        ctk.CTkLabel(self.empty, text="Add a webcam, a capture card, a phone (Iriun, DroidCam) or a network camera "
                                      "(RTSP). Each one records continuously and marks motion so you can jump to it.",
                     font=font(13), text_color=MUTED, wraplength=560, justify="center").pack(padx=24, pady=(4, 12))
        ctk.CTkButton(self.empty, text="Add camera", height=38, font=font(13, "bold"),
                      command=lambda: self.edit(None)).pack(pady=(0, 28))
        self._build_events()
        self.tiles = {}
        self._layout = None
        self._events_key = None

    # ---- tiles ---------------------------------------------------------------------------------------------
    def _rebuild(self):
        cams = self.app.cfg["cameras"]
        key = tuple((c["id"], c["name"]) for c in cams)
        if key == self._layout:
            return
        self._layout = key
        for tile in self.tiles.values():
            tile.destroy()
        self.tiles = {}
        for i, cam in enumerate(cams):
            tile = CameraTile(self.grid_frame, self, cam)
            tile.grid(row=i // 2, column=i % 2, sticky="nsew", padx=7, pady=7)
            self.tiles[cam["id"]] = tile
        if cams:
            self.empty.grid_forget()
            self.events.grid(row=3, column=0, columnspan=2, sticky="ew", padx=28, pady=(14, 28))
        else:
            self.empty.grid(row=2, column=0, columnspan=2, sticky="ew", padx=28, pady=7)
            self.events.grid_forget()
        self._fill_camera_choice()

    def on_show(self):
        self._rebuild()
        self._events_key = None

    def reload(self):
        self._layout = None
        self.on_show()

    def on_status(self, _desktop_status):
        self._rebuild()
        station = self.app.station
        for s in station.status():
            tile, rec = self.tiles.get(s["id"]), station.recorders.get(s["id"])
            if tile and rec:
                tile.refresh(s, rec)
        update(self.pause_all, text="Resume all" if station.any_paused() else "Pause all",
               image=icon("play" if station.any_paused() else "pause", 16))
        self._refresh_events()

    def _toggle_all(self):
        station = self.app.station
        station.pause_all(not station.any_paused())

    def open_folder(self, cam_id):
        cam = next((c for c in self.app.cfg["cameras"] if c["id"] == cam_id), None)
        root = camera_root(self.app.cfg)
        if cam and root:
            path = os.path.join(root, cam["folder"])
            os.makedirs(path, exist_ok=True)
            os.startfile(path)

    def edit(self, cam_id):
        cam = next((c for c in self.app.cfg["cameras"] if c["id"] == cam_id), None)
        CameraDialog(self, self.app, cam, on_saved=self.reload)

    # ---- motion events -----------------------------------------------------------------------------------
    def _build_events(self):
        self.events = Card(self)
        top = ctk.CTkFrame(self.events, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(top, text="Motion", font=font(15, "bold", display=True)).pack(side="left")
        self.day_var = tk.StringVar(value="Today")
        self.day_menu = ctk.CTkOptionMenu(top, variable=self.day_var, values=["Today"], width=140,
                                          command=lambda _v: self._force_events())
        self.day_menu.pack(side="right")
        self.cam_var = tk.StringVar()
        self.cam_menu = ctk.CTkOptionMenu(top, variable=self.cam_var, values=[""], width=180,
                                          command=lambda _v: self._camera_changed())
        self.cam_menu.pack(side="right", padx=8)
        self.timeline = tk.Canvas(self.events, height=46, bg=CARD, highlightthickness=0, cursor="hand2")
        self.timeline.pack(fill="x", padx=20, pady=(4, 2))
        self.timeline.bind("<Configure>", lambda _e: self._draw_timeline())
        self.timeline.bind("<Button-1>", self._timeline_click)
        self.event_list = ctk.CTkFrame(self.events, fg_color="transparent")
        self.event_list.pack(fill="x", padx=14, pady=(4, 14))
        self.day_events, self.day_chunks = [], []

    def _fill_camera_choice(self):
        names = [c["name"] for c in self.app.cfg["cameras"]]
        if not names:
            return
        self.cam_menu.configure(values=names)
        if self.cam_var.get() not in names:
            self.cam_var.set(names[0])
            self._camera_changed()

    def _camera(self):
        return next((c for c in self.app.cfg["cameras"] if c["name"] == self.cam_var.get()), None)

    def _camera_changed(self):
        cam, root = self._camera(), camera_root(self.app.cfg)
        days = []
        if cam and root:
            days = [d for d, _ in stats.list_days(os.path.join(root, cam["folder"]))]
        today = datetime.date.today().strftime("%Y-%m-%d")
        values = ["Today"] + [d for d in days if d != today]
        self.day_menu.configure(values=values)
        if self.day_var.get() not in values:
            self.day_var.set("Today")
        self._force_events()

    def _force_events(self):
        self._events_key = None
        self._refresh_events()

    def _refresh_events(self):
        cam, root = self._camera(), camera_root(self.app.cfg)
        if not cam or not root:
            return
        day = self.day_var.get()
        day = datetime.date.today().strftime("%Y-%m-%d") if day == "Today" else day
        folder = os.path.join(root, cam["folder"], day)
        try:
            mtime = os.stat(os.path.join(folder, motion.EventLog.NAME)).st_mtime
        except OSError:
            mtime = 0
        key = (cam["id"], day, mtime, int(time.time() // 60))
        if key == self._events_key:
            return
        self._events_key = key
        self.day_events = motion.read_events(folder)
        self.day_chunks = [c["path"] for c in stats.list_chunks(folder)]
        self.day_start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        self._draw_timeline()
        for w in self.event_list.winfo_children():
            w.destroy()
        if not self.day_events:
            text = "No motion yet." if cam["motion"] else "Motion detection is off for this camera (Edit to turn it on)."
            ctk.CTkLabel(self.event_list, text=text, font=font(12), text_color=MUTED, anchor="w").pack(fill="x", padx=6, pady=4)
            return
        for ev in list(reversed(self.day_events))[:30]:
            row = ctk.CTkFrame(self.event_list, fg_color=CARD_HI, corner_radius=8)
            row.pack(fill="x", pady=2)
            start = time.strftime("%H:%M:%S", time.localtime(ev["start"]))
            length = max(1, int(ev["end"] - ev["start"]))
            ctk.CTkLabel(row, text=start, font=font(13, "bold"), width=80, anchor="w").pack(side="left", padx=(12, 4), pady=6)
            ctk.CTkLabel(row, text=f"{length} s  ·  {ev['peak'] * 100:.0f}% of the picture moved", font=font(12),
                         text_color=MUTED, anchor="w").pack(side="left")
            ghost_button(row, "Play", "play", height=28, width=80,
                         command=lambda e=ev: self._play(e)).pack(side="right", padx=8, pady=4)

    def _draw_timeline(self):
        c = self.timeline
        c.delete("all")
        w = max(100, c.winfo_width())
        c.create_rectangle(0, 8, w, 30, fill=INPUT, outline=BORDER)
        for hour in range(0, 25, 3):
            x = hour / 24 * (w - 1)
            c.create_line(x, 30, x, 34, fill=SUBTLE)
            c.create_text(min(max(x, 12), w - 12), 41, text=f"{hour:02d}", fill=SUBTLE, font=("Segoe UI", 8))
        start = getattr(self, "day_start", None)
        if start is None:
            return
        for path in self.day_chunks:   # recorded stretches
            s = motion.chunk_start(path)
            if s is None:
                continue
            try:
                e = os.path.getmtime(path)
            except OSError:
                continue
            x0, x1 = (s - start) / 86400 * w, (e - start) / 86400 * w
            c.create_rectangle(x0, 14, max(x0 + 1, x1), 24, fill="#2B3240", outline="")
        for ev in self.day_events:
            x0, x1 = (ev["start"] - start) / 86400 * w, (ev["end"] - start) / 86400 * w
            c.create_rectangle(x0, 9, max(x0 + 2, x1), 29, fill=ACCENT, outline="")

    def _timeline_click(self, e):
        if not self.day_events or not hasattr(self, "day_start"):
            return
        t = self.day_start + e.x / max(1, self.timeline.winfo_width()) * 86400
        nearest = min(self.day_events, key=lambda ev: abs((ev["start"] + ev["end"]) / 2 - t))
        if abs(nearest["start"] - t) < 86400 / 48:      # within half an hour on the bar
            self._play(nearest)

    def _play(self, ev):
        path, offset = motion.locate(ev["start"], self.day_chunks)
        if path:
            self.app.open_recording(path, at=offset)

class CameraDialog(ctk.CTkToplevel):
    """Add or edit one camera."""
    def __init__(self, page, app, cam, on_saved):
        super().__init__(page)
        self.app, self.on_saved = app, on_saved
        self.new = cam is None
        self.cam = dict(cam or CAMERA_DEFAULTS)
        self.devices = {"video": [], "audio": []}
        self.title("Add camera" if self.new else f"Edit {self.cam['name']}")
        self.geometry("720x760")
        self.minsize(640, 560)
        self.transient(page.winfo_toplevel())
        self.after(100, self.grab_set)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 0))
        self.body = body
        self.v = {}
        self._build()
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, sticky="ew", padx=20, pady=14)
        self.msg = ctk.CTkLabel(foot, text="", font=font(12), text_color=MUTED, anchor="w", wraplength=360, justify="left")
        self.msg.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(foot, text="Save", width=110, height=36, font=font(13, "bold"), command=self.save).pack(side="right")
        ghost_button(foot, "Cancel", width=90, command=self.destroy).pack(side="right", padx=8)
        if not self.new:
            ghost_button(foot, "Remove", width=96, command=self.remove).pack(side="right")
        threading.Thread(target=self._load_devices, daemon=True).start()
        self._kind_changed()

    # ---- building ----------------------------------------------------------------------------------------
    def _var(self, key, kind=tk.StringVar, value=None):
        var = kind(value=self.cam[key] if value is None else value)
        self.v[key] = var
        return var

    def _card(self, title):
        ctk.CTkLabel(self.body, text=title, font=font(15, "bold", display=True), anchor="w").pack(fill="x", padx=8, pady=(14, 6))
        card = Card(self.body)
        card.pack(fill="x", padx=8)
        spacer(card, 6, top=True)
        return card

    @staticmethod
    def _row(parent, title, desc=None):
        """A SettingRow whose description fits beside its control in this narrower window."""
        row = SettingRow(parent, title, desc)
        if desc:
            row.desc.configure(wraplength=330)
        return row

    def _switch(self, card, key, title, desc=None):
        row = self._row(card, title, desc)
        ctk.CTkSwitch(row.right, text="", variable=self._var(key, tk.BooleanVar), width=46).pack()
        return row

    def _build(self):
        card = self._card("Camera")
        row = self._row(card, "Name", "Shown on the Cameras page. Recordings go in a folder with this name.")
        ctk.CTkEntry(row.right, textvariable=self._var("name"), width=240).pack()
        divider(card)
        row = self._row(card, "Type")
        self.kind = self._var("kind", value="Network camera" if self.cam["kind"] == "url" else "Webcam or capture device")
        ctk.CTkSegmentedButton(row.right, values=["Webcam or capture device", "Network camera"], variable=self.kind,
                               font=font(12), command=lambda _v: self._kind_changed()).pack()
        divider(card)
        self.device_box = ctk.CTkFrame(card, fg_color=CARD, corner_radius=0)
        row = self._row(self.device_box, "Device", "Webcams, capture cards and phone webcam apps (Iriun, DroidCam) "
                                                    "that Windows sees.")
        self.device_var = tk.StringVar(value=self.cam.get("label") or "Looking for devices…")
        self.device_menu = ctk.CTkOptionMenu(row.right, variable=self.device_var, values=[self.device_var.get()], width=240)
        self.device_menu.pack(side="left")
        ghost_button(row.right, "", "refresh", width=36, command=lambda: threading.Thread(
            target=self._load_devices, daemon=True).start()).pack(side="left", padx=(6, 0))
        self.url_box = ctk.CTkFrame(card, fg_color=CARD, corner_radius=0)
        row = self._row(self.url_box, "Address", "rtsp://192.168.1.20:554/stream1 or an http:// MJPEG address. See "
                                                  "your camera's manual or app for its RTSP address.")
        ctk.CTkEntry(row.right, textvariable=self._var("url"), width=300).pack()
        row = self._row(self.url_box, "Login", "Stored encrypted for your Windows account and never shown in logs.")
        ctk.CTkEntry(row.right, textvariable=self._var("user"), width=140, placeholder_text="User").pack(side="left")
        self.password = ctk.CTkEntry(row.right, width=140, show="•", placeholder_text="Password" if not self.cam["secret"] else "Unchanged")
        self.password.pack(side="left", padx=(6, 0))
        self.test_row = ctk.CTkFrame(card, fg_color=CARD, corner_radius=0)
        self.test_row.pack(fill="x", padx=20, pady=(8, 12))
        self.preview = ctk.CTkLabel(self.test_row, text="", image=placeholder(), width=TILE_W, height=TILE_H,
                                    fg_color="#0B0D11", corner_radius=8)
        self.preview.pack(side="left")
        side = ctk.CTkFrame(self.test_row, fg_color="transparent")
        side.pack(side="left", padx=14, fill="y")
        ghost_button(side, "Test", "check", width=110, command=self.test).pack(anchor="w")
        self.test_info = ctk.CTkLabel(side, text="Grabs one picture to check the camera works.", font=font(12),
                                      text_color=MUTED, wraplength=180, justify="left", anchor="w")
        self.test_info.pack(anchor="w", pady=8)

        card = self._card("Recording")
        self._switch(card, "enabled", "Record this camera", "Turn off to keep the camera set up without recording.")
        divider(card)
        row = self._row(card, "Resolution", "The most it records. Smaller cameras record at their own size.")
        self._var("height", value=f"{self.cam['height']}p" if f"{self.cam['height']}p" in HEIGHTS else "720p")
        ctk.CTkSegmentedButton(row.right, values=list(HEIGHTS), variable=self.v["height"], font=font(12)).pack()
        divider(card)
        row = self._row(card, "Frame rate", "15 FPS is smooth enough for most rooms and costs half of 30.")
        self._var("fps", value=str(self.cam["fps"]) if str(self.cam["fps"]) in FPS_CHOICES else "15")
        ctk.CTkSegmentedButton(row.right, values=FPS_CHOICES, variable=self.v["fps"], font=font(12)).pack()
        divider(card)
        row = self._row(card, "Format", "Same as desktop uses your Settings choice (Ultra means Smallest here: H.266 "
                                         "can't keep up with camera video). Smallest (AV1) is the best fit for cameras.")
        self._var("preset", value=invert(FORMATS)[self.cam["preset"]])
        ctk.CTkOptionMenu(row.right, variable=self.v["preset"], values=list(FORMATS), width=240).pack()
        divider(card)
        self._switch(card, "keep_stream", "Keep the camera's own stream",
                                     "Network cameras only. Saves the stream as the camera sends it: next to no CPU, "
                                     "larger files. Turn off to make it smaller (re-encoded with the format above).")
        divider(card)
        self._switch(card, "timestamp", "Date and time in the picture",
                     "Burns the clock into the video, visible in any player. Needs re-encoding.")
        divider(card)
        self.audio_row = self._switch(card, "audio", "Record sound", "The camera's microphone, or a network camera's own audio. "
                                                    "Off by default.")
        self.mic_box = ctk.CTkFrame(card, fg_color=CARD, corner_radius=0)
        self.mic_box.pack(fill="x")
        row = self._row(self.mic_box, "Microphone", "Picked automatically from the camera's name.")
        self.mic_var = tk.StringVar(value="Automatic")
        self.mic_menu = ctk.CTkOptionMenu(row.right, variable=self.mic_var, values=["Automatic"], width=240)
        self.mic_menu.pack()
        spacer(card)

        card = self._card("Motion")
        self._switch(card, "motion", "Detect motion", "Marks motion on the timeline so you can jump straight to it.")
        divider(card)
        row = self._row(card, "Sensitivity", "Higher catches smaller movements. Lower ignores swaying plants and "
                                              "flickering light.")
        self.sens = self._var("sensitivity", tk.IntVar)
        self.sens_label = ctk.CTkLabel(row.right, text=str(self.cam["sensitivity"]), font=font(13, "bold"), width=30)
        self.sens_label.pack(side="right")
        ctk.CTkSlider(row.right, from_=1, to=10, number_of_steps=9, variable=self.sens, width=200,
                      command=lambda v: self.sens_label.configure(text=str(int(v)))).pack(side="right", padx=8)
        divider(card)
        self._switch(card, "notify", "Notify me", "A Windows notification when motion starts, at most once a minute.")
        divider(card)
        self._switch(card, "quiet_saver", "Save space when nothing moves",
                     "Drops near-identical frames (at least one a second stays). Saved 11% on a still webcam scene; "
                     "sensor noise keeps most frames different. Needs re-encoding.")
        spacer(card)

        card = self._card("Pausing")
        self._switch(card, "hotkey", "Pause with the camera hotkey",
                     "Set the hotkey in Settings > General. Handy for your own webcam before a call.")
        divider(card)
        self._row(card, "Pause while these apps are open", "Frees the camera for them, then resumes when they close. "
                                                            "One program name per line, e.g. zoom.exe.")
        self.apps_box = ctk.CTkTextbox(card, height=72, font=font(12), border_width=1)
        self.apps_box.insert("1.0", "\n".join(self.cam["pause_apps"]))
        self.apps_box.pack(fill="x", padx=20, pady=(0, 14))

    # ---- behavior ----------------------------------------------------------------------------------------
    def _kind_changed(self):
        url = self.kind.get() == "Network camera"
        (self.url_box if url else self.device_box).pack(fill="x", before=self.test_row)
        (self.device_box if url else self.url_box).pack_forget()
        if url:
            self.mic_box.pack_forget()
        else:
            self.mic_box.pack(fill="x", after=self.audio_row)

    def _load_devices(self):
        devices = cameras.list_devices(self.app.station.ff)
        self.app.ui_queue.put(lambda: self._set_devices(devices))

    def _set_devices(self, devices):
        if not self.winfo_exists():
            return
        self.devices = devices
        names = [n for n, _ in devices["video"]] or ["No devices found"]
        self.device_menu.configure(values=names)
        if self.device_var.get() not in names:
            current = next((n for n, d in devices["video"] if d == self.cam["device"]), None)
            self.device_var.set(current or names[0])
        mics = ["Automatic"] + [n for n, _ in devices["audio"]]
        self.mic_menu.configure(values=mics)
        current_mic = next((n for n, d in devices["audio"] if d == self.cam["mic"]), None)
        auto = cameras.pair_mic(self.cam.get("label") or self.device_var.get(), devices["audio"])
        self.mic_var.set(current_mic if current_mic and self.cam["mic"] != auto else "Automatic")

    def collect(self):
        cam = dict(self.cam)
        url = self.kind.get() == "Network camera"
        cam["kind"] = "url" if url else "device"
        cam["name"] = self.v["name"].get().strip() or "Camera"
        for key in ("enabled", "keep_stream", "timestamp", "audio", "motion", "notify", "quiet_saver", "hotkey"):
            cam[key] = bool(self.v[key].get())
        cam["height"] = HEIGHTS[self.v["height"].get()]
        cam["fps"] = int(self.v["fps"].get())
        cam["preset"] = FORMATS[self.v["preset"].get()]
        cam["sensitivity"] = int(self.sens.get())
        cam["pause_apps"] = [l.strip().lower() for l in self.apps_box.get("1.0", "end").splitlines() if l.strip()]
        if url:
            cam["url"], cam["user"] = self.v["url"].get().strip(), self.v["user"].get().strip()
            if self.password.get():
                cam["secret"] = cameras.protect(self.password.get())
            cam["device"] = cam["mic"] = cam["label"] = ""
        else:
            name = self.device_var.get()
            dev = next((d for n, d in self.devices["video"] if n == name), None)
            if dev:
                cam["device"], cam["label"] = dev, name
            mic = self.mic_var.get()
            cam["mic"] = (next((d for n, d in self.devices["audio"] if n == mic), "") if mic != "Automatic"
                          else cameras.pair_mic(cam["label"] or name, self.devices["audio"]) or "")
            cam["url"] = cam["user"] = cam["secret"] = ""
        return cam

    def _problem(self, cam):
        if cam["kind"] == "url" and not cameras.is_url(cam["url"]):
            return "Enter an address starting with rtsp:// or http://"
        if cam["kind"] == "device" and not cam["device"]:
            return "Pick a device (plug it in, then press refresh)"
        if cam["audio"] and cam["kind"] == "device" and not cam["mic"]:
            return "No microphone found for this camera. Pick one or turn off sound."
        if not camera_root(self.app.cfg):
            return "Choose a recordings folder in Settings first"
        return None

    def test(self):
        cam = self.collect()
        problem = self._problem(cam)
        if problem and "microphone" not in problem:
            self.test_info.configure(text=problem, text_color=YELLOW)
            return
        rec = self.app.station.recorders.get(cam["id"]) if not self.new else None
        if rec and rec.enc and cam["kind"] == "device":
            self.test_info.configure(text="This camera is recording now. Pause it on the Cameras page to test it.",
                                     text_color=YELLOW)
            return
        self.test_info.configure(text="Testing…", text_color=MUTED)
        def work():
            frame, info, error = cameras.test(self.app.station.ff, cam)
            self.app.ui_queue.put(lambda: self._tested(frame, info, error))
        threading.Thread(target=work, daemon=True).start()

    def _tested(self, frame, info, error):
        if not self.winfo_exists():
            return
        if frame:
            self.preview.configure(image=to_image(frame))
            text = "Works."
            if info:
                codec, w, h, fps = info
                text += f" {codec.upper()} {w}×{h} at {fps:g} FPS."
            self.test_info.configure(text=text, text_color=GREEN)
        else:
            self.test_info.configure(text=error or "No picture", text_color=YELLOW)

    def save(self):
        cam = self.collect()
        problem = self._problem(cam)
        if problem:
            self.msg.configure(text=problem, text_color=YELLOW)
            return
        cams = [c for c in self.app.cfg["cameras"]]
        if self.new:
            cam["id"], cam["folder"] = "", ""
            cam = validate_camera(cam, [c["folder"] for c in cams])
            cams.append(cam)
        else:
            cams = [cam if c["id"] == cam["id"] else c for c in cams]
        self.app.save_settings(self.app.cfg | {"cameras": cams})
        self.destroy()
        self.on_saved()

    def remove(self):
        dialog = ctk.CTkInputDialog(text=f"Type REMOVE to remove {self.cam['name']}. Its recordings stay on disk.",
                                    title="Remove camera")
        if (dialog.get_input() or "").strip().upper() != "REMOVE":
            return
        cams = [c for c in self.app.cfg["cameras"] if c["id"] != self.cam["id"]]
        self.app.save_settings(self.app.cfg | {"cameras": cams})
        self.destroy()
        self.on_saved()
