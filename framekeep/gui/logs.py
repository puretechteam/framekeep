# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import os
import customtkinter as ctk
from .. import log as logmod
from ..paths import APPDATA_DIR, LOG_FILE
from .theme import GREEN, MUTED, PURPLE, SUBTLE, TEXT, YELLOW, Card, font, ghost_button, mono

TAG_COLORS = {"RED": "#F87171", "GREEN": GREEN, "CYAN": "#22D3EE", "YELLOW": YELLOW, "MAGENTA": PURPLE, "RESET": TEXT}

class LogsPage(ctk.CTkFrame):
    def __init__(self, master, win):
        super().__init__(master, fg_color="transparent")
        self.win, self.app = win, win.app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(26, 14))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Logs", font=font(26, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(titles, text="Everything FrameKeep does, as it happens.", font=font(13), text_color=MUTED, anchor="w").pack(anchor="w")
        ghost_button(head, "Open log file", "info", command=lambda: os.path.exists(LOG_FILE) and os.startfile(LOG_FILE)).pack(side="right")
        ghost_button(head, "Open data folder", "open_folder", command=lambda: os.startfile(APPDATA_DIR)).pack(side="right", padx=8)

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 28))
        self.text = ctk.CTkTextbox(card, font=mono(12), fg_color="transparent", wrap="word", border_width=0)
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.text.tag_config("stamp", foreground=SUBTLE)
        for name, color in TAG_COLORS.items():
            self.text.tag_config(name, foreground=color)
        self.text.configure(state="disabled")
        self._listener = lambda *line: self.app.ui_queue.put(lambda: self._append(*line))
        logmod.subscribe(self._listener)
        self.bind("<Destroy>", lambda e: e.widget is self and logmod.unsubscribe(self._listener))  # the window closes to the tray

    def _append(self, stamp, tag, msg, color):
        if not self.winfo_exists():
            return
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.insert("end", f"{stamp}  ", "stamp")
        self.text.insert("end", f"[{tag}]", color)
        self.text.insert("end", f"  {msg}\n")
        if float(self.text.index("end")) > 2000:
            self.text.delete("1.0", "200.0")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")
