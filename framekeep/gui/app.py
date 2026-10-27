# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import customtkinter as ctk
from .. import __version__
from ..paths import ICON_PATH
from . import theme
from .theme import ACCENT, BORDER, CARD_HI, MUTED, SIDEBAR, STATE_STYLE, TEXT, font, icon

theme.apply_theme()

NAV = [("dashboard", "Dashboard"), ("recordings", "Recordings"), ("settings", "Settings"),
       ("benchmark", "Benchmark"), ("logs", "Logs")]

class MainWindow(ctk.CTk):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.title("FrameKeep")
        self.geometry("1180x760")
        self.minsize(980, 640)
        try:
            self.iconbitmap(ICON_PATH)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._told_about_tray = False
        self.status = app.recorder.status()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {}
        self.current = None

        if app.first_run:
            from .wizard import Wizard
            self.wizard = Wizard(self, app, on_done=self._wizard_done)
            self.wizard.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            self.wizard = None
        self.show_page("dashboard")
        self.after(100, self._pump)
        self.after(500, self._poll)

    # ---- layout ------------------------------------------------------------
    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=228, fg_color=SIDEBAR, corner_radius=0, border_width=0)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(len(NAV) + 2, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 22))
        ctk.CTkLabel(brand, text="", image=icon("record", 26, ACCENT)).pack(side="left")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=10)
        ctk.CTkLabel(names, text="FrameKeep", font=font(18, "bold", display=True), anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text=f"v{__version__}", font=font(11), text_color=MUTED, anchor="w").pack(anchor="w")

        self.nav_buttons = {}
        for i, (key, label) in enumerate(NAV, start=1):
            b = ctk.CTkButton(bar, text=f"  {label}", image=icon(key, 18, MUTED), compound="left", anchor="w",
                              height=42, corner_radius=10, fg_color="transparent", hover_color=CARD_HI,
                              text_color=MUTED, font=font(14), command=lambda k=key: self.show_page(k))
            b.grid(row=i, column=0, sticky="ew", padx=12, pady=2)
            self.nav_buttons[key] = b

        foot = ctk.CTkFrame(bar, fg_color=CARD_HI, corner_radius=12, border_width=1, border_color=BORDER)
        foot.grid(row=len(NAV) + 3, column=0, sticky="ew", padx=12, pady=16)
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10, 4))
        self.side_dot = ctk.CTkLabel(row, text="", image=icon("dot", 14, MUTED), width=14)
        self.side_dot.pack(side="left")
        self.side_state = ctk.CTkLabel(row, text="Ready", font=font(13, "bold"), anchor="w")
        self.side_state.pack(side="left", padx=6)
        self.side_button = ctk.CTkButton(foot, text="Start recording", image=icon("record", 14, "#FFFFFF"), compound="left",
                                         height=34, font=font(13, "bold"), command=self.app.recorder.toggle)
        self.side_button.pack(fill="x", padx=12, pady=(4, 12))

    def show_page(self, key):
        if key not in self.pages:
            from . import pages
            cls = {"dashboard": pages.DashboardPage, "recordings": pages.RecordingsPage, "settings": pages.SettingsPage,
                   "benchmark": pages.BenchmarkPage, "logs": pages.LogsPage}[key]
            self.pages[key] = cls(self.content, self)
        if self.current:
            self.pages[self.current].grid_remove()
            self.nav_buttons[self.current].configure(fg_color="transparent", text_color=MUTED, image=icon(self.current, 18, MUTED))
        self.current = key
        page = self.pages[key]
        page.grid(row=0, column=0, sticky="nsew")
        self.nav_buttons[key].configure(fg_color=CARD_HI, text_color=TEXT, image=icon(key, 18, ACCENT))
        if hasattr(page, "on_show"):
            page.on_show()

    # ---- window/tray -------------------------------------------------------
    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        page = self.pages.get(self.current)
        if page is not None and hasattr(page, "on_show"):
            page.on_show()  # numbers were frozen while hidden; refresh them immediately

    def hide_to_tray(self):
        self.withdraw()
        if not self._told_about_tray:
            self._told_about_tray = True
            self.app.tray.notify("FrameKeep is still running in the tray. Right-click the icon to quit.")

    def _wizard_done(self):
        self.wizard.place_forget()
        self.wizard.destroy()
        self.wizard = None
        for page in self.pages.values():
            if hasattr(page, "reload"):
                page.reload()
        self.show_page("dashboard")

    # ---- periodic updates --------------------------------------------------
    def _pump(self):
        self.app._drain_ui_queue()
        try:
            self.after(100, self._pump)
        except Exception:
            pass  # window destroyed during quit

    def _poll(self):
        self.status = self.app.recorder.status()
        if self.state() in ("withdrawn", "iconic"):
            self.after(500, self._poll)  # hidden in the tray or minimized: keep the status fresh, skip all drawing
            return
        label, color = STATE_STYLE[self.status["state"]]
        self.side_dot.configure(image=icon("dot", 14, color))
        self.side_state.configure(text=label)
        active = self.status["state"] != "idle"
        self.side_button.configure(text="Stop recording" if active else "Start recording",
                                   image=icon("stop" if active else "record", 14, "#FFFFFF"),
                                   fg_color=CARD_HI if active else ACCENT, hover_color=BORDER if active else theme.ACCENT_HOVER,
                                   border_width=1 if active else 0)
        page = self.pages.get(self.current)
        if page is not None and hasattr(page, "on_status"):
            page.on_status(self.status)
        self.after(250, self._poll)  # visible: keep the timer and live sizes smooth
