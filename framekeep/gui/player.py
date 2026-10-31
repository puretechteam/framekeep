# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""The FrameKeep player: a day of desktop and camera recordings on one timeline, side by side, on the
puretech-player engine (FFmpeg decoding, Direct3D drawing, WASAPI sound)."""

import datetime, os, threading, time
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from puretech_player import SPEEDS, Grid, Source, VideoView, export_clip, fmt_wall, plan_clip
from .. import playback
from ..engine import FFmpeg
from ..paths import ICON_PATH
from .theme import (ACCENT, BLUE, BORDER, CARD, GREEN, INPUT, MUTED, PURPLE, SUBTLE, TEXT, YELLOW, font, ghost_button,
                    icon, update)

LANE_COLORS = (ACCENT, BLUE, GREEN, PURPLE)
LANE_H, LANE_GAP, AXIS_H = 9, 4, 16
TICK_MS = 100
SEEK_STEP = 5.0

def speed_label(s):
    return f"{s:g}x"

class PlayerWindow(ctk.CTkToplevel):
    def __init__(self, win, app):
        super().__init__(win)
        self.win, self.app = win, app
        self.title("FrameKeep player")
        self.geometry("1280x820")
        self.minsize(900, 600)
        self.after(250, self._set_icon)     # CTkToplevel sets its own icon shortly after creation
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.ff = FFmpeg(app.cfg["ffmpeg_path"])
        self.engine = None                  # puretech_player.Grid of the shown sources
        self.views, self.shown = [], []     # VideoViews, and the source ids they show (playable ones only)
        self.day, self.selected, self.audio_id = None, [], None
        self.mark_in = self.mark_out = None
        self.events, self.lanes = [], []    # motion events; (name, color, [(start, end)]) per selected source
        self.zoom_day = False               # the bar fits the footage; the zoom button shows the whole day
        self._closed = False
        self._build()
        self._bind_keys()
        self.after(TICK_MS, self._tick)

    def _set_icon(self):
        try:
            self.iconbitmap(ICON_PATH)
        except Exception:
            pass

    # ---- layout ----------------------------------------------------------------------------------------------
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        self.day_var = tk.StringVar()
        self.day_menu = ctk.CTkOptionMenu(top, variable=self.day_var, values=["-"], width=130,
                                          command=lambda _v: self._day_changed())
        self.day_menu.pack(side="left")
        self.src_frame = ctk.CTkFrame(top, fg_color="transparent")
        self.src_frame.pack(side="left", padx=12)
        self.src_vars = {}
        ghost_button(top, "Delete", None, width=70, command=self._delete_layout).pack(side="right")
        ghost_button(top, "Save layout", "save", width=120, command=self._save_layout).pack(side="right", padx=6)
        self.layout_var = tk.StringVar(value="Layouts")
        self.layout_menu = ctk.CTkOptionMenu(top, variable=self.layout_var, values=["Layouts"], width=150,
                                             command=self._apply_layout)
        self.layout_menu.pack(side="right")
        self.audio_var = tk.StringVar(value="Sound: off")
        self.audio_menu = ctk.CTkOptionMenu(top, variable=self.audio_var, values=["Sound: off"], width=170,
                                            command=lambda _v: self._audio_changed())
        self.audio_menu.pack(side="right", padx=12)

        self.stage = ctk.CTkFrame(self, fg_color="#000000", corner_radius=10)
        self.stage.grid(row=1, column=0, sticky="nsew", padx=14)
        self.stage.grid_columnconfigure((0, 1), weight=1, uniform="col")
        self.empty = ctk.CTkLabel(self.stage, text="", font=font(14), text_color=MUTED)

        self.bar = tk.Canvas(self, height=AXIS_H + 4 * (LANE_H + LANE_GAP) + 6, bg=CARD, highlightthickness=0, cursor="hand2")
        self.bar.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 0))
        self.bar.bind("<Configure>", lambda _e: self._draw_bar())
        self.bar.bind("<Button-1>", self._bar_seek)
        self.bar.bind("<B1-Motion>", self._bar_seek)

        ctl = ctk.CTkFrame(self, fg_color="transparent")
        ctl.grid(row=3, column=0, sticky="ew", padx=14, pady=(8, 12))
        def button(text, cmd, width=44, **kw):
            b = ghost_button(ctl, text, None, width=width, height=32, command=cmd, **kw)
            b.pack(side="left", padx=(0, 6))
            return b
        button("◀ Motion", lambda: self.jump_motion(-1), width=84)
        button("−5 s", lambda: self.nudge(-SEEK_STEP), width=56)
        button("◀|", lambda: self.step(-1))
        self.play_btn = ctk.CTkButton(ctl, text="", image=icon("pause", 18, "#FFFFFF"), width=56, height=32,
                                      command=self.toggle)
        self.play_btn.pack(side="left", padx=(0, 6))
        button("|▶", lambda: self.step(1))
        button("+5 s", lambda: self.nudge(SEEK_STEP), width=56)
        button("Motion ▶", lambda: self.jump_motion(1), width=84)
        self.time_label = ctk.CTkLabel(ctl, text="--:--:--", font=font(15, "bold"), width=96)
        self.time_label.pack(side="left", padx=(8, 4))
        self.speed_var = tk.StringVar(value=speed_label(1.0))
        ctk.CTkOptionMenu(ctl, variable=self.speed_var, values=[speed_label(s) for s in SPEEDS], width=86,
                          command=lambda v: self.set_speed(float(v[:-1]))).pack(side="left", padx=6)
        self.gap_btn = ghost_button(ctl, "Skip gap", None, width=90, height=32, command=self.skip_gap)
        ghost_button(ctl, "Export clip", "share", width=124, height=32, command=self.export).pack(side="right")
        button_out = ghost_button(ctl, "Out", None, width=52, height=32, command=lambda: self.mark("out"))
        button_out.pack(side="right", padx=6)
        ghost_button(ctl, "In", None, width=52, height=32, command=lambda: self.mark("in")).pack(side="right")
        self.zoom_btn = ghost_button(ctl, "Whole day", None, width=120, height=32, command=self.toggle_zoom)
        self.zoom_btn.pack(side="right", padx=6)
        self.state_label = ctk.CTkLabel(ctl, text="", font=font(12), text_color=MUTED)
        self.state_label.pack(side="left", padx=8)

    def _bind_keys(self):
        keys = {"<space>": lambda e: self.toggle(), "<Left>": lambda e: self.nudge(-SEEK_STEP),
                "<Right>": lambda e: self.nudge(SEEK_STEP), "<Shift-Left>": lambda e: self.step(-1),
                "<Shift-Right>": lambda e: self.step(1), "<comma>": lambda e: self.step(-1),
                "<period>": lambda e: self.step(1), "<bracketleft>": lambda e: self.change_speed(-1),
                "<bracketright>": lambda e: self.change_speed(1), "<m>": lambda e: self.jump_motion(1),
                "<M>": lambda e: self.jump_motion(-1), "<i>": lambda e: self.mark("in"),
                "<o>": lambda e: self.mark("out"), "<e>": lambda e: self.export(), "<g>": lambda e: self.skip_gap(),
                "<Escape>": lambda e: self.close()}
        for seq, fn in keys.items():
            self.bind(seq, fn)

    # ---- what is shown ---------------------------------------------------------------------------------------
    def load(self, ids=None, day=None, at=None):
        """Show these sources (ids) on this day, starting at wall time `at` (defaults: last used, newest day)."""
        cfg = self.app.cfg
        known = [s[0] for s in playback.sources(cfg)]
        days = playback.days(cfg) or [datetime.date.today().strftime("%Y-%m-%d")]
        self.day = day if day in days else (day or days[0])
        if self.day not in days:
            days = sorted(days + [self.day], reverse=True)
        self.day_menu.configure(values=days)
        self.day_var.set(self.day)
        last = cfg["player_last"]
        ids = [i for i in (ids or last.get("sources") or known[:1]) if i in known][:playback.MAX_TILES] or known[:1]
        self.selected = ids
        if self.audio_id not in ids:
            self.audio_id = last.get("audio") if last.get("audio") in ids else ids[0] if ids else None
        self._rebuild_sources()
        self._refresh_layouts()
        self._open(at)

    def _rebuild_sources(self):
        for w in self.src_frame.winfo_children():
            w.destroy()
        self.src_vars = {}
        for sid, name, _ in playback.sources(self.app.cfg):
            var = tk.BooleanVar(value=sid in self.selected)
            ctk.CTkCheckBox(self.src_frame, text=name, variable=var, width=20, font=font(12),
                            command=lambda s=sid: self._source_toggled(s)).pack(side="left", padx=(0, 12))
            self.src_vars[sid] = var

    def _source_toggled(self, sid):
        picked = [s for s, v in self.src_vars.items() if v.get()]
        if len(picked) > playback.MAX_TILES:
            self.src_vars[sid].set(False)
            self._say(f"Up to {playback.MAX_TILES} sources at once")
            return
        if not picked:
            self.src_vars[sid].set(True)
            return
        self.selected = [s for s in self.selected if s in picked] + [s for s in picked if s not in self.selected]
        self._open(self.position())

    def _day_changed(self):
        self.day = self.day_var.get()
        self.mark_in = self.mark_out = None
        self._open(None)

    def _names(self):
        return {sid: name for sid, name, _ in playback.sources(self.app.cfg)}

    def _open(self, at):
        """(Re)build the tiles and the engine for the selected sources and day, then play from `at`."""
        self._close_engine()
        for w in self.stage.winfo_children():
            if w is not self.empty:
                w.destroy()
        self.views, self.shown = [], []
        names = {sid: (name, folder) for sid, name, folder in playback.sources(self.app.cfg)}
        files = {sid: playback.day_files(names[sid][1], self.day) for sid in self.selected if sid in names}
        cells = playback.tiles(len(self.selected))
        sources = []
        rows = max(r + rs for r, _, rs, _ in cells)
        for row in range(playback.MAX_TILES):
            self.stage.grid_rowconfigure(row, weight=1 if row < rows else 0, uniform="row" if row < rows else "")
        for (row, col, rs, cs), sid in zip(cells, self.selected):
            tile = tk.Frame(self.stage, bg="#000000")
            tile.grid(row=row, column=col, rowspan=rs, columnspan=cs, sticky="nsew", padx=2, pady=2)
            color = LANE_COLORS[self.selected.index(sid) % len(LANE_COLORS)]
            tk.Label(tile, text=names[sid][0], bg="#000000", fg=color, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
            if files.get(sid):
                view = VideoView(tile)
                view.pack(fill="both", expand=True)
                self.views.append(view)
                self.shown.append(sid)
                sources.append(Source.from_files(files[sid], self.ff.ffprobe, name=names[sid][0]))
            else:
                tk.Label(tile, text="No recording this day", bg="#000000", fg=SUBTLE,
                         font=("Segoe UI", 12)).pack(fill="both", expand=True)
        self.lanes = self._lanes(files)
        self.events = playback.motion_events(self.app.cfg, self.selected, self.day)
        self._refresh_audio_menu()
        if not sources:
            self.empty.configure(text="Nothing recorded on this day for the chosen sources.")
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
            self._draw_bar()
            return
        self.empty.place_forget()
        audio = self.shown.index(self.audio_id) if self.audio_id in self.shown else None
        try:
            self.engine = Grid(sources, ffmpeg=self.ff.ffmpeg, views=self.views, audio=audio)
        except Exception as e:
            self._say(f"Can't play: {e}")
            return
        start = at if at is not None else max(self.engine.start, playback.day_bounds(self.day)[0])
        self.engine.seek(start)
        self.engine.set_speed(float(self.speed_var.get()[:-1]))
        self.engine.play()
        self._remember()
        self._draw_bar()

    def _lanes(self, files):
        """(name, color, [(start, end)]) per selected source: when it recorded, without probing, so a day of
        one-minute chunks draws at once. A chunk starts at its name and ends when it was last written, at the
        latest when the next one starts or after the chunk length."""
        names, chunk = self._names(), self.app.cfg["chunk_minutes"] * 60 + 60
        out = []
        for i, sid in enumerate(self.selected):
            timed = [(t, p) for t, p in ((playback.wall_time(p), p) for p in files.get(sid, [])) if t is not None]
            starts = [t for t, _ in timed]
            spans = []
            for j, (s, path) in enumerate(timed):
                try:
                    written = os.path.getmtime(path)
                except OSError:
                    written = s + chunk
                e = max(s + 1, min(starts[j + 1] if j + 1 < len(starts) else written, written, s + chunk))
                if spans and s - spans[-1][1] < 3:
                    spans[-1] = (spans[-1][0], e)
                else:
                    spans.append((s, e))
            out.append((names.get(sid, sid), LANE_COLORS[i % len(LANE_COLORS)], spans))
        return out

    # ---- sound -------------------------------------------------------------------------------------------------
    def _refresh_audio_menu(self):
        names = self._names()
        values = ["Sound: off"] + [f"Sound: {names[s]}" for s in self.shown]
        self.audio_menu.configure(values=values)
        self.audio_var.set(f"Sound: {names[self.audio_id]}" if self.audio_id in self.shown else "Sound: off")

    def _audio_changed(self):
        names = {f"Sound: {n}": s for s, n in self._names().items()}
        self.audio_id = names.get(self.audio_var.get())
        if self.engine:
            self.engine.set_audio(self.shown.index(self.audio_id) if self.audio_id in self.shown else None)
        self._remember()

    # ---- layouts -----------------------------------------------------------------------------------------------
    def _refresh_layouts(self):
        names = [l["name"] for l in self.app.cfg["player_layouts"]]
        self.layout_menu.configure(values=names or ["No saved layouts"])
        self.layout_var.set("Layouts")

    def _apply_layout(self, name):
        layout = next((l for l in self.app.cfg["player_layouts"] if l["name"] == name), None)
        self.layout_var.set("Layouts")
        if layout:
            self.selected, self.audio_id = list(layout["sources"]), layout.get("audio")
            self._rebuild_sources()
            self._open(self.position())

    def _save_layout(self):
        name = ctk.CTkInputDialog(title="Save layout", text="Name this layout (for example \"Desk and door\"):").get_input()
        name = (name or "").strip()[:40]
        if not name:
            return
        layouts = [l for l in self.app.cfg["player_layouts"] if l["name"] != name]
        layouts.append({"name": name, "sources": list(self.selected), "audio": self.audio_id})
        self.app.save_view_state(player_layouts=layouts)
        self._refresh_layouts()
        self._say(f"Saved \"{name}\"")

    def _delete_layout(self):
        names = [l["name"] for l in self.app.cfg["player_layouts"]]
        if not names:
            return
        name = ctk.CTkInputDialog(title="Delete layout", text="Layout to delete: " + ", ".join(names)).get_input()
        if name and name.strip() in names:
            self.app.save_view_state(player_layouts=[l for l in self.app.cfg["player_layouts"] if l["name"] != name.strip()])
            self._refresh_layouts()

    def _remember(self):
        last = {"sources": list(self.selected), "audio": self.audio_id}
        if last != self.app.cfg["player_last"]:
            self.app.save_view_state(player_last=last)

    # ---- transport ---------------------------------------------------------------------------------------------
    def position(self):
        """Wall time on screen (the grid's shared clock, which follows frame steps too)."""
        return self.engine.position if self.engine else None

    def toggle(self):
        if not self.engine:
            return
        if any(p.playing for p in self.engine.players):
            self.engine.pause()
        else:
            self.engine.play()

    def seek(self, t):
        if self.engine:
            self.engine.seek(t)

    def nudge(self, seconds):
        if self.engine:
            self.seek(self.position() + seconds)

    def step(self, direction):
        if self.engine:
            self.engine.step(direction)

    def set_speed(self, speed):
        self.speed_var.set(speed_label(speed))
        if self.engine:
            self.engine.set_speed(speed)

    def change_speed(self, direction):
        current = float(self.speed_var.get()[:-1])
        i = min(range(len(SPEEDS)), key=lambda k: abs(SPEEDS[k] - current))
        self.set_speed(SPEEDS[min(max(i + direction, 0), len(SPEEDS) - 1)])

    def skip_gap(self):
        if self.engine:
            ahead = [p.next_media() for p in self.engine.players if p.next_media()]
            if ahead:
                self.engine.seek(min(ahead))

    def jump_motion(self, direction):
        pos = self.position()
        t = playback.next_event(self.events, pos, direction) if pos is not None else None
        if t is None:
            self._say("No more motion this day" if self.events else "No motion events for these sources")
            return
        self.seek(t - 2)

    def toggle_zoom(self):
        self.zoom_day = not self.zoom_day
        update(self.zoom_btn, text="Fit to footage" if self.zoom_day else "Whole day")   # what a click switches to
        self._draw_bar()

    # ---- clips -------------------------------------------------------------------------------------------------
    def mark(self, which):
        pos = self.position()
        if pos is None:
            return
        if which == "in":
            self.mark_in = pos
            if self.mark_out is not None and self.mark_out <= pos:
                self.mark_out = None
        else:
            self.mark_out = pos
            if self.mark_in is not None and self.mark_in >= pos:
                self.mark_in = None
        self._say(f"{'In' if which == 'in' else 'Out'} at {fmt_wall(pos)}")
        self._draw_bar()

    def export(self):
        if not self.engine or not self.shown:
            return
        pos = self.position()
        t_in = self.mark_in if self.mark_in is not None else pos
        t_out = self.mark_out if self.mark_out is not None and self.mark_out > t_in else t_in + 30
        main = self.shown[0]
        source = self.engine.players[0].source
        name = self._names()[main]
        self._say("Finding keyframes…")
        def plan():
            try:
                result = plan_clip(source, t_in, t_out, ffprobe=self.ff.ffprobe)
            except Exception as e:
                result = e
            self._later(lambda: self._confirm_export(result, name))
        threading.Thread(target=plan, daemon=True).start()

    def _confirm_export(self, plan, name):
        if isinstance(plan, Exception):
            self._say(f"Can't export: {plan}")
            return
        note = plan.describe()
        if plan.gaps:
            note += f"\n{len(plan.gaps)} gap(s) without recording are left out."
        if not messagebox.askokcancel("Export clip", f"{name}\n{note}\n\nThe recording itself is copied, so the clip "
                                      "keeps full quality and saves in a moment.", parent=self):
            self._say("")
            return
        folder = os.path.join(self.app.cfg["log_root"], "Clips") if self.app.cfg["log_root"] else os.path.expanduser("~")
        os.makedirs(folder, exist_ok=True)
        ext = plan.container or ".mp4"
        path = filedialog.asksaveasfilename(parent=self, initialdir=folder, initialfile=playback.clip_name(name, plan.start, ext),
                                            defaultextension=ext, filetypes=[("Video", f"*{ext}")])
        if not path:
            self._say("")
            return
        self._say("Saving clip…")
        def run():
            try:
                files = export_clip(plan, path, ffmpeg=self.ff.ffmpeg)
                self._later(lambda: self._exported(files))
            except Exception as e:
                message = f"Export failed: {e}"
                self._later(lambda: self._say(message))
        threading.Thread(target=run, daemon=True).start()

    def _exported(self, files):
        self._say(f"Saved {os.path.basename(files[0])}" + (f" and {len(files) - 1} more" if len(files) > 1 else ""))
        if files and messagebox.askyesno("Clip saved", "Show the clip in its folder?", parent=self):
            self.app.show_in_folder(files[0])

    # ---- the time bar ------------------------------------------------------------------------------------------
    def _range(self):
        d0, d1 = playback.day_bounds(self.day) if self.day else (0, 86400)
        if self.zoom_day:
            return d0, d1
        spans = [s for _, _, lane in self.lanes for s in lane]
        if not spans:
            return d0, d1
        t0, t1 = min(s for s, _ in spans), max(e for _, e in spans)
        pad = max(60, (t1 - t0) * 0.03)
        return max(d0, t0 - pad), min(d1, t1 + pad)

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        w = max(100, c.winfo_width())
        t0, t1 = self._range()
        span = t1 - t0
        step = next(s for s in (60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600) if span / s <= 12) if span < 86400 else 3 * 3600
        first = (int(t0) // step + 1) * step - (time.localtime(t0).tm_gmtoff % step if step >= 3600 else 0)
        t = first
        while t < t1:
            x = playback.x_for(t, t0, t1, w)
            c.create_line(x, AXIS_H - 4, x, AXIS_H, fill=SUBTLE)
            c.create_text(x + 3, 1, text=fmt_wall(t, seconds=False), anchor="nw", fill=MUTED, font=("Segoe UI", 8))
            t += step
        c.create_line(0, AXIS_H, w, AXIS_H, fill=BORDER)
        for i, (name, color, spans) in enumerate(self.lanes):
            y = AXIS_H + 4 + i * (LANE_H + LANE_GAP)
            c.create_rectangle(0, y, w, y + LANE_H, fill=INPUT, outline="")
            for s, e in spans:
                if e > t0 and s < t1:
                    c.create_rectangle(playback.x_for(s, t0, t1, w), y, max(playback.x_for(e, t0, t1, w),
                                       playback.x_for(s, t0, t1, w) + 1), y + LANE_H, fill=color, outline="")
        lanes_bottom = AXIS_H + 4 + max(1, len(self.lanes)) * (LANE_H + LANE_GAP)
        for s, e, _ in self.events:
            if t0 <= s <= t1:
                x = playback.x_for(s, t0, t1, w)
                c.create_line(x, AXIS_H + 1, x, lanes_bottom, fill=YELLOW, width=2)
        if self.mark_in is not None or self.mark_out is not None:
            a = self.mark_in if self.mark_in is not None else self.mark_out
            b = self.mark_out if self.mark_out is not None else self.mark_in
            c.create_rectangle(playback.x_for(a, t0, t1, w), AXIS_H, playback.x_for(b, t0, t1, w) + 2, lanes_bottom,
                               outline=TEXT, dash=(3, 2))
        pos = self.position()
        if pos is not None and t0 <= pos <= t1:
            x = playback.x_for(pos, t0, t1, w)
            c.create_line(x, 0, x, lanes_bottom + 2, fill="#FFFFFF", width=2, tags="head")

    def _bar_time(self, x):
        t0, t1 = self._range()
        return playback.t_for(x, t0, t1, max(100, self.bar.winfo_width()))

    def _bar_seek(self, e):
        self.seek(self._bar_time(e.x))

    # ---- upkeep ------------------------------------------------------------------------------------------------
    def _tick(self):
        if self._closed:
            return
        if self.engine:
            pos = self.position()
            update(self.time_label, text=fmt_wall(pos))
            playing = any(p.playing for p in self.engine.players)
            update(self.play_btn, image=icon("pause" if playing else "play", 18, "#FFFFFF"))
            gap = all(p.state in ("gap", "ended") for p in self.engine.players)
            if gap and not self.gap_btn.winfo_ismapped():
                self.gap_btn.pack(side="left", padx=6)
            elif not gap and self.gap_btn.winfo_ismapped():
                self.gap_btn.pack_forget()
            if not self._status_hold or time.time() > self._status_hold:
                update(self.state_label, text="Not recording here" if gap else "")
            self._move_head(pos)
        self.after(TICK_MS, self._tick)

    _status_hold = 0.0

    def _move_head(self, pos):
        c = self.bar
        t0, t1 = self._range()
        w = max(100, c.winfo_width())
        c.delete("head")
        if t0 <= pos <= t1:
            x = playback.x_for(pos, t0, t1, w)
            lanes_bottom = AXIS_H + 4 + max(1, len(self.lanes)) * (LANE_H + LANE_GAP)
            c.create_line(x, 0, x, lanes_bottom + 2, fill="#FFFFFF", width=2, tags="head")

    def _say(self, text):
        update(self.state_label, text=text)
        self._status_hold = time.time() + 4 if text else 0.0

    def _later(self, fn):
        """Run fn on the Tk thread (from a worker thread)."""
        self.app.ui_queue.put(lambda: None if self._closed else fn())

    def _close_engine(self):
        engine, self.engine = self.engine, None
        if engine:
            threading.Thread(target=engine.close, daemon=True).start()   # stopping decoders can take a moment

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._close_engine()
        self.destroy()
        self.win.player_closed()
