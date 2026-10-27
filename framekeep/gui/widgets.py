# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import threading
import customtkinter as ctk
from ..engine import PRESET_INFO
from .theme import ACCENT, ACCENT_DIM, BORDER, CARD, GREEN, MUTED, SUBTLE, TEXT, Badge, font, icon

PRESET_ORDER = ["ultra", "smallest", "smaller", "compatible", "lowcpu"]

class PresetPicker(ctk.CTkFrame):
    """Radio-card list of presets with availability and benchmark numbers."""
    def __init__(self, master, app, variable, report=None, on_change=None):
        super().__init__(master, fg_color="transparent")
        self.app, self.var, self.on_change = app, variable, on_change
        self.report = report
        self.available = None
        self.rows = {}
        for key in PRESET_ORDER:
            self.rows[key] = self._row(key)
        self.var.trace_add("write", lambda *_: self._paint())
        self._paint()
        threading.Thread(target=self._probe, daemon=True).start()

    def _probe(self):
        avail = self.app.recorder.ff.available_presets()
        self.app.ui_queue.put(lambda: self._set_available(avail))

    def _set_available(self, avail):
        if self.winfo_exists():
            self.available = avail
            self._paint()

    def set_report(self, report):
        self.report = report
        self._paint()

    def _row(self, key):
        name, codec, desc = PRESET_INFO[key]
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER, cursor="hand2")
        card.pack(fill="x", pady=4)
        card.grid_columnconfigure(1, weight=1)
        dot = ctk.CTkLabel(card, text="", width=22)
        dot.grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=12)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=1, sticky="w", pady=(10, 0))
        ctk.CTkLabel(top, text=name, font=font(14, "bold")).pack(side="left")
        Badge(top, codec, MUTED).pack(side="left", padx=8)
        rec = Badge(top, "Recommended", GREEN)
        desc_label = ctk.CTkLabel(card, text=desc, font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=520)
        desc_label.grid(row=1, column=1, sticky="w", pady=(0, 10))
        stat = ctk.CTkLabel(card, text="", font=font(13, "bold"), text_color=MUTED)
        stat.grid(row=0, column=2, rowspan=2, padx=16)
        for w in (card, dot, top, desc_label, stat, *top.winfo_children()):
            w.bind("<Button-1>", lambda _e, k=key: self._pick(k))
        return {"card": card, "dot": dot, "rec": rec, "stat": stat, "desc": desc_label}

    def _pick(self, key):
        if self.available is not None and not self.available.get(key):
            return
        self.var.set(key)
        if self.on_change:
            self.on_change(key)

    def _paint(self):
        results = (self.report or {}).get("results", {})
        recommended = (self.report or {}).get("recommended")
        for key, r in self.rows.items():
            selected = self.var.get() == key
            ok = self.available is None or bool(self.available.get(key))
            r["card"].configure(fg_color=ACCENT_DIM if selected else CARD, border_color=ACCENT if selected else BORDER)
            r["dot"].configure(image=icon("record" if selected else "dot", 18, ACCENT if selected else SUBTLE))
            if key == recommended:
                r["rec"].pack(side="left")
            else:
                r["rec"].pack_forget()
            if not ok:
                r["stat"].configure(text="Not available", text_color=SUBTLE)
            elif key in results:
                r["stat"].configure(text=f"{results[key]['mb_per_hour']:.0f} MB/hr", text_color=TEXT if selected else MUTED)
            else:
                r["stat"].configure(text="", text_color=MUTED)

class SettingRow(ctk.CTkFrame):
    """Title + description on the left, a control on the right."""
    def __init__(self, master, title, desc=None):
        super().__init__(master, fg_color="transparent")
        self.pack(fill="x", padx=20, pady=8)
        self.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text=title, font=font(13, "bold"), anchor="w").pack(anchor="w")
        if desc:
            self.desc = ctk.CTkLabel(left, text=desc, font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=460)
            self.desc.pack(anchor="w")
        self.right = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)  # CTkFrame defaults to 200x200 when empty
        self.right.grid(row=0, column=1, sticky="e", padx=(16, 0))

def spacer(master, height=8, top=False):
    """Vertical breathing room inside a Card, kept off the card's 1px border."""
    ctk.CTkFrame(master, height=height, width=1, fg_color="transparent").pack(pady=(3, 0) if top else (0, 3))

def divider(master):
    ctk.CTkFrame(master, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x", padx=20, pady=4)
