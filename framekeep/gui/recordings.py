# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Read-only recordings browser, for desktop recordings and each camera. Deleting and moving files stays manual, in
Explorer (only the optional camera cleanup in Settings ever deletes, and only old camera footage)."""

import datetime, os, tkinter as tk
from concurrent.futures import ThreadPoolExecutor
import customtkinter as ctk
from PIL import Image
from .. import motion, stats
from ..config import camera_root
from ..log import log
from .theme import (ACCENT, BORDER, CARD, CARD_HI, INPUT, MUTED, Badge, Card, font, ghost_button, icon)

THUMB_W, THUMB_H = 240, 135
CODEC_NAMES = {"h264": "H.264", "hevc": "H.265", "av1": "AV1", "vvc": "H.266", "mjpeg": "MJPG"}
AUDIO_NAMES = {"opus": "Opus", "aac": "AAC"}

def fit_thumb(path, scale=1.0):
    """The cached thumbnail at the size it's drawn at (cached files are 2x, for high-DPI screens)."""
    w, h = round(THUMB_W * scale), round(THUMB_H * scale)
    with Image.open(path) as src:
        img = src.convert("RGB")
    img.thumbnail((w, h))
    canvas = Image.new("RGB", (w, h), INPUT)
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return canvas

class ChunkCard(Card):
    def __init__(self, master, page, chunk, recording, motion_count=0):
        super().__init__(master, corner_radius=12)
        self.page, self.chunk, self.info = page, chunk, None
        self.thumb = ctk.CTkLabel(self, text="", image=page.placeholder, corner_radius=8, fg_color=INPUT)
        self.thumb.pack(padx=10, pady=(10, 8))
        title_row = ctk.CTkFrame(self, fg_color="transparent")
        title_row.pack(fill="x", padx=14)
        name = os.path.splitext(chunk["name"])[0]
        stamp, _, mon = name.partition("_")
        ctk.CTkLabel(title_row, text=stamp.replace("-", ":"), font=font(15, "bold", display=True), anchor="w").pack(side="left")
        if mon:
            Badge(title_row, mon.replace("mon", "Display "), MUTED).pack(side="left", padx=8)
        if motion_count:
            Badge(title_row, f"{motion_count} motion", ACCENT).pack(side="left", padx=8)
        self.codec_badge = Badge(title_row, "…", MUTED)
        self.codec_badge.pack(side="right")
        self.meta = ctk.CTkLabel(self, text=stats.human_bytes(chunk["size"]), font=font(12), text_color=MUTED, anchor="w")
        self.meta.pack(fill="x", padx=14, pady=(2, 8))
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=10, pady=(0, 12))
        if recording:
            Badge(actions, "● Recording now", ACCENT).pack(side="left", padx=4)
            self.codec_badge.configure(text="live")
            return
        ctk.CTkButton(actions, text="Play", image=icon("play", 14, "#FFFFFF"), compound="left", width=70, height=32,
                      font=font(13, "bold"), command=self.play).pack(side="left", padx=3)
        ghost_button(actions, "", "folder", width=34, height=32,
                     command=lambda: page.app.show_in_folder(chunk["path"])).pack(side="left", padx=3)
        self.share_btn = ghost_button(actions, "Share copy", "share", height=32, command=self.share)
        self.share_btn.pack(side="right", padx=3)

    def set_info(self, info, thumb):
        if not self.winfo_exists():
            return
        self.info = info or {}
        codec = self.info.get("codec")
        if codec:
            text = CODEC_NAMES.get(codec, codec.upper())
            if self.info.get("audio"):     # recorded with desktop audio
                text += " · " + AUDIO_NAMES.get(self.info["audio"], self.info["audio"].upper())
            self.codec_badge.configure(text=text, text_color=ACCENT if codec == "vvc" else MUTED)
        dur = self.info.get("duration")
        parts = [stats.human_duration(dur)] if dur else []
        if self.info.get("width"):
            parts.append(f"{self.info['width']}×{self.info['height']}")
        parts.append(stats.human_bytes(self.chunk["size"]))
        self.meta.configure(text="  ·  ".join(parts))
        if thumb:
            self.thumb.configure(image=ctk.CTkImage(thumb, size=(THUMB_W, THUMB_H)))
        if codec == "h264" and self.info.get("audio") in (None, "aac") and hasattr(self, "share_btn"):
            self.share_btn.pack_forget()  # already plays everywhere

    def play(self):
        self.page.app.open_recording(self.chunk["path"], (self.info or {}).get("codec"))

    def share(self):
        self.share_btn.configure(text="Copying…", state="disabled")
        self.page.make_share_copy(self)

class RecordingsPage(ctk.CTkFrame):
    def __init__(self, master, win):
        super().__init__(master, fg_color="transparent")
        self.win, self.app = win, win.app
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.cache = stats.ProbeCache()
        self.scale = ctk.ScalingTracker.get_widget_scaling(self)
        self.placeholder = ctk.CTkImage(Image.new("RGB", (1, 1), INPUT), size=(THUMB_W, THUMB_H))  # one, shared by every card
        self._generation = 0    # bumped whenever the cards are replaced, so stale thumbnail work is skipped
        self.selected = None
        self.cards = []
        self.columns = 0
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(26, 14))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Recordings", font=font(26, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(titles, text="FrameKeep never edits or deletes desktop recordings. Old camera footage is only "
                                  "deleted if you turn that on in Settings.",
                     font=font(13), text_color=MUTED, anchor="w", justify="left", wraplength=400).pack(anchor="w")
        ghost_button(head, "Refresh", "refresh", width=110, command=self.refresh).pack(side="right")
        ghost_button(head, "Open folder", "open_folder", width=130, command=self._open_root).pack(side="right", padx=8)
        ghost_button(head, "Open player", "play", width=130, command=self._open_player).pack(side="right")
        self.source_var = tk.StringVar(value="Desktop")
        self.source_menu = ctk.CTkOptionMenu(head, variable=self.source_var, values=["Desktop"], width=180,
                                             command=lambda _v: self._source_changed())

        self.day_list = ctk.CTkScrollableFrame(self, width=230, fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=12)
        self.day_list.grid(row=1, column=0, sticky="nsw", padx=(28, 8), pady=(0, 28))
        self.grid_area = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.grid_area.grid(row=1, column=1, sticky="nsew", padx=(8, 20), pady=(0, 28))
        # reflow when the visible part of the card grid resizes (the scrollable frame's own inner frame sizes to its
        # content, and the page's <Configure> comes before the grid area has its new width)
        self.grid_area._parent_canvas.bind("<Configure>", self._on_resize, add="+")
        self.day_buttons = {}
        self.empty = ctk.CTkLabel(self.grid_area, text="", font=font(14), text_color=MUTED)

    def on_show(self):
        self.refresh()

    def on_hide(self):
        """Drop the cards and their thumbnails while the page isn't visible; refresh() rebuilds them."""
        self._clear_cards()
        self.cache.flush()

    def _clear_cards(self):
        self._generation += 1
        for card in self.cards:
            card.destroy()
        self.cards = []

    def reload(self):
        self.selected = None

    def _camera(self):
        return next((c for c in self.app.cfg["cameras"] if c["name"] == self.source_var.get()), None)

    def _folder(self):
        """The folder whose day folders are listed: the recordings folder, or the chosen camera's folder."""
        cam, root = self._camera(), camera_root(self.app.cfg)
        if cam and root:
            return os.path.join(root, cam["folder"])
        return self.app.cfg["log_root"]

    def _source_changed(self):
        self.selected = None
        self.refresh()

    def _open_player(self):
        """The player on the day shown here, starting with this source."""
        cam = self._camera()
        self.app.open_player([cam["id"] if cam else "desktop"], self.selected)

    def _open_root(self):
        root = self._folder()
        if root and os.path.isdir(root):
            os.startfile(root)

    def refresh(self):
        names = [c["name"] for c in self.app.cfg["cameras"]]
        if names:
            self.source_menu.configure(values=["Desktop", *names])
            self.source_menu.pack(side="right", padx=(0, 8))
        else:
            self.source_menu.pack_forget()
        if self.source_var.get() not in ["Desktop", *names]:
            self.source_var.set("Desktop")
        for w in self.day_list.winfo_children():
            w.destroy()
        self.day_buttons = {}
        days = stats.list_days(self._folder())
        if not days:
            self._show_day(None)
            return
        today = datetime.date.today()
        for name, path in days:
            d = datetime.date.fromisoformat(name)
            label = "Today" if d == today else "Yesterday" if (today - d).days == 1 else d.strftime("%A")
            item = ctk.CTkFrame(self.day_list, fg_color="transparent", corner_radius=8, cursor="hand2")
            item.pack(fill="x", padx=4, pady=2)
            title = ctk.CTkLabel(item, text=label, font=font(13, "bold"), anchor="w")
            title.pack(fill="x", padx=12, pady=(8, 0))
            sub = ctk.CTkLabel(item, text=d.strftime("%B %d, %Y").replace(" 0", " "), font=font(12), text_color=MUTED, anchor="w")
            sub.pack(fill="x", padx=12, pady=(0, 8))
            for w in (item, title, sub):
                w.bind("<Button-1>", lambda _e, n=name, p=path: self._show_day(n, p))
                w.bind("<Enter>", lambda _e, i=item, n=name: n != self.selected and i.configure(fg_color=CARD_HI))
                w.bind("<Leave>", lambda _e, i=item, n=name: n != self.selected and i.configure(fg_color="transparent"))
            self.day_buttons[name] = item
        if self.selected not in dict(days):
            self.selected = days[0][0]
        self._show_day(self.selected, dict(days)[self.selected])

    def _show_day(self, name, path=None):
        self._clear_cards()
        if name is None:
            self.empty.configure(text="No recordings yet. Start recording and they'll show up here.")
            self.empty.grid(row=0, column=0, pady=60)
            return
        self.empty.grid_forget()
        for n, b in self.day_buttons.items():
            b.configure(fg_color=CARD_HI if n == name else "transparent")
        self.selected = name
        recording = set(self.win.status.get("files", [])) | set(self.app.station.active_files())
        chunks = stats.list_chunks(path)
        events = motion.read_events(path) if self._camera() else []
        if not chunks:
            self.empty.configure(text="This day has no recordings.")
            self.empty.grid(row=0, column=0, pady=60)
        for chunk in chunks:
            start = motion.chunk_start(chunk["path"]) if events else None
            moving = sum(1 for e in events if start is not None and start <= e["start"] <= chunk["mtime"])
            card = ChunkCard(self.grid_area, self, chunk, chunk["path"] in recording, moving)
            self.cards.append(card)
            if chunk["path"] not in recording:
                self.pool.submit(self._load, card, chunk, self._generation)
        self.columns = 0
        self._layout()

    def _load(self, card, chunk, generation):
        """Background: probe + thumbnail (read-only on the recording, writes only to AppData)."""
        if generation != self._generation:
            return      # the page moved on before this card's turn came
        info = self.cache.get(chunk)
        if info is None:
            info = self.app.recorder.ff.probe(chunk["path"])
            if info:
                self.cache.put(chunk, info)
        tp = stats.thumb_path(chunk)
        if os.path.exists(tp):
            stats.touch(tp)
        elif self.app.recorder.ff.ffmpeg:
            self.app.recorder.ff.thumbnail(chunk["path"], tp, width=THUMB_W * 2)
        thumb = None
        if os.path.exists(tp):
            try:
                thumb = fit_thumb(tp, self.scale)
            except OSError:
                pass
        self.app.ui_queue.put(lambda: card.set_info(info, thumb))

    def _columns(self, page_width):
        canvas = getattr(self.grid_area, "_parent_canvas", None)  # the visible part of the scrollable frame
        usable = canvas.winfo_width() if canvas is not None and canvas.winfo_width() > 1 else page_width - self.day_list.winfo_width() - 100
        card = (self.cards[0].winfo_reqwidth() if self.cards else THUMB_W + 24) + 16
        return max(1, usable // card)

    def _on_resize(self, _event):
        cols = self._columns(self.winfo_width())
        if cols != self.columns:
            self._layout(cols)

    def _layout(self, cols=None):
        if cols is None:
            self.update_idletasks()  # a freshly shown page has no real width yet
            cols = self._columns(self.winfo_width())
        self.columns = cols
        for i, card in enumerate(self.cards):
            card.grid(row=i // cols, column=i % cols, padx=8, pady=8, sticky="n")

    def make_share_copy(self, card):
        def work():
            log("SHARE", f"Creating H.264 copy of {card.chunk['name']}", "MAGENTA")
            dst = self.app.recorder.ff.share_copy(card.chunk["path"])
            if dst:
                log("SHARE", f"Created {dst}", "GREEN")
                self.app.ui_queue.put(self.refresh)
            else:
                log("ERROR", f"Could not create a copy of {card.chunk['name']}", "RED")
                self.app.ui_queue.put(lambda: card.share_btn.configure(text="Copy failed", state="normal"))
        self.pool.submit(work)
