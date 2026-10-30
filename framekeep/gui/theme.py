# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import functools, os
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

BG = "#0E1014"
SIDEBAR = "#111419"
CARD = "#171A21"
CARD_HI = "#1E222B"
INPUT = "#12151B"
BORDER = "#252A34"
TEXT = "#E9ECF1"
MUTED = "#8C93A1"
SUBTLE = "#5E6573"
ACCENT = "#FF4D5E"
ACCENT_HOVER = "#FF6B79"
ACCENT_DIM = "#3A1A20"
GREEN = "#34D399"
YELLOW = "#FBBF24"
PURPLE = "#A78BFA"
BLUE = "#60A5FA"

STATE_STYLE = {   # state -> (label, color)
    "recording": ("Recording", ACCENT),
    "starting": ("Starting…", ACCENT),
    "paused": ("Paused", YELLOW),
    "auto_paused": ("Auto-paused", PURPLE),
    "idle": ("Ready", MUTED),
}
REASON_TEXT = {"idle": "you're away", "locked": "the screen is locked", "private": "a private app is in focus"}

ICONS = {
    "dashboard": 0xECA5, "recordings": 0xE8B2, "cameras": 0xE722, "settings": 0xE713, "benchmark": 0xEC4A, "logs": 0xE8FD,
    "pause": 0xE769, "play": 0xE768, "folder": 0xE8B7, "open_folder": 0xED25, "share": 0xE72D, "lock": 0xE72E,
    "privacy": 0xED1A, "idle": 0xE823, "drive": 0xEDA2, "monitor": 0xE7F4, "bolt": 0xE945, "leaf": 0xE8BE,
    "info": 0xE946, "warning": 0xE7BA, "check": 0xE73E, "keyboard": 0xE765, "calendar": 0xE787, "timer": 0xE916,
    "chevron": 0xE76C, "save": 0xE74E, "refresh": 0xE72C, "compress": 0xE73F, "shield": 0xEA18, "video": 0xE714,
}
_FONT_FILES = [r"C:\Windows\Fonts\SegoeIcons.ttf", r"C:\Windows\Fonts\segmdl2.ttf"]

def apply_theme():
    """Re-skin customtkinter's defaults before any widget is created."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    t = ctk.ThemeManager.theme
    pair = lambda c: [c, c]
    t["CTk"]["fg_color"] = t["CTkToplevel"]["fg_color"] = pair(BG)
    t["CTkFrame"].update(fg_color=pair(CARD), top_fg_color=pair(CARD_HI), border_color=pair(BORDER), corner_radius=12)
    t["CTkButton"].update(fg_color=pair(ACCENT), hover_color=pair(ACCENT_HOVER), text_color=pair("#FFFFFF"),
                          text_color_disabled=pair(SUBTLE), border_color=pair(BORDER), corner_radius=8)
    t["CTkLabel"]["text_color"] = pair(TEXT)
    t["CTkEntry"].update(fg_color=pair(INPUT), border_color=pair(BORDER), text_color=pair(TEXT),
                         placeholder_text_color=pair(SUBTLE), corner_radius=8)
    t["CTkCheckBox"].update(fg_color=pair(ACCENT), hover_color=pair(ACCENT_HOVER), border_color=pair(SUBTLE),
                            checkmark_color=pair("#FFFFFF"), text_color=pair(TEXT), corner_radius=5)
    t["CTkSwitch"].update(fg_color=pair(BORDER), progress_color=pair(ACCENT), button_color=pair(TEXT),
                          button_hover_color=pair("#FFFFFF"), text_color=pair(TEXT))
    t["CTkRadioButton"].update(fg_color=pair(ACCENT), hover_color=pair(ACCENT_HOVER), border_color=pair(SUBTLE), text_color=pair(TEXT))
    t["CTkProgressBar"].update(fg_color=pair(BORDER), progress_color=pair(ACCENT))
    t["CTkSlider"].update(fg_color=pair(BORDER), progress_color=pair(ACCENT), button_color=pair(TEXT), button_hover_color=pair("#FFFFFF"))
    t["CTkOptionMenu"].update(fg_color=pair(INPUT), button_color=pair(CARD_HI), button_hover_color=pair(BORDER), text_color=pair(TEXT))
    t["CTkSegmentedButton"].update(fg_color=pair(INPUT), selected_color=pair(CARD_HI), selected_hover_color=pair(BORDER),
                                   unselected_color=pair(INPUT), unselected_hover_color=pair(CARD), text_color=pair(TEXT),
                                   corner_radius=8, border_width=3)
    t["CTkScrollbar"].update(fg_color="transparent", button_color=pair(BORDER), button_hover_color=pair(SUBTLE))
    t["CTkTextbox"].update(fg_color=pair(INPUT), border_color=pair(BORDER), text_color=pair(TEXT),
                           scrollbar_button_color=pair(BORDER), scrollbar_button_hover_color=pair(SUBTLE))
    t["CTkScrollableFrame"]["label_fg_color"] = pair(CARD)
    t["DropdownMenu"].update(fg_color=pair(CARD_HI), hover_color=pair(BORDER), text_color=pair(TEXT))
    t["CTkFont"].update(family="Segoe UI Variable Text", size=13)  # already resolved to this platform's entry
    quiet_trackers()

def quiet_trackers():
    """CustomTkinter polls the Windows light/dark setting every 30 ms while a window is open, and display scaling
    every 100 ms. FrameKeep is always dark, so the first poll is never started, and scaling is checked once a
    second (moving the window to a screen with different scaling still re-scales it)."""
    ctk.AppearanceModeTracker.update_loop_running = True
    ctk.ScalingTracker.update_loop_interval = 1000

def release_trackers():
    """Forget the destroyed window. CustomTkinter's trackers keep a reference to every window ever created,
    which kept each closed window's whole Tcl interpreter alive (~2 MB per open/close)."""
    ctk.AppearanceModeTracker.app_list.clear()
    ctk.AppearanceModeTracker.callback_list.clear()
    ctk.ScalingTracker.window_widgets_dict.clear()
    ctk.ScalingTracker.window_dpi_scaling_dict.clear()
    ctk.ScalingTracker.update_loop_running = False

def font(size=13, weight="normal", display=False):
    return ctk.CTkFont(family="Segoe UI Variable Display" if display else "Segoe UI Variable Text", size=size, weight=weight)

def mono(size=12):
    return ctk.CTkFont(family="Cascadia Mono", size=size)

@functools.lru_cache(maxsize=None)
def _icon_font(px):
    for path in _FONT_FILES:
        if os.path.exists(path):
            return ImageFont.truetype(path, px)
    return None

@functools.lru_cache(maxsize=None)
def icon(name, size=18, color=TEXT):
    """A crisp CTkImage of a Windows icon-font glyph (or a drawn shape for record/stop)."""
    scale = 3
    px = size * scale
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if name == "record":
        m = px * 0.18
        d.ellipse((m, m, px - m, px - m), fill=color)
    elif name == "stop":
        m = px * 0.22
        d.rounded_rectangle((m, m, px - m, px - m), radius=px * 0.12, fill=color)
    elif name == "dot":
        m = px * 0.3
        d.ellipse((m, m, px - m, px - m), fill=color)
    else:
        f = _icon_font(int(px * 0.82))
        if f:
            ch = chr(ICONS[name])
            box = d.textbbox((0, 0), ch, font=f)
            d.text(((px - (box[2] - box[0])) / 2 - box[0], (px - (box[3] - box[1])) / 2 - box[1]), ch, font=f, fill=color)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))

_UNSET = object()

def update(widget, **kw):
    """configure() only the options that changed. CustomTkinter redraws a widget on every configure() call, even
    with identical values, and the status refresh runs twice a second."""
    last = widget.__dict__.setdefault("_fk_applied", {})
    changed = {k: v for k, v in kw.items() if last.get(k, _UNSET) != v}
    if changed:
        widget.configure(**changed)
        last.update(changed)

def set_progress(bar, value, color=ACCENT):
    """CTkProgressBar draws a dot at 0; hide it by matching the track color."""
    update(bar, progress_color=color if value > 0 else BORDER)
    value = round(value, 3)
    if bar.__dict__.get("_fk_value") != value:
        bar._fk_value = value
        bar.set(value)

def shorten(path, limit=70):
    return path if len(path) <= limit else path[:limit // 2 - 2] + "…" + path[-(limit // 2 - 1):]

# ---- reusable widgets -------------------------------------------------------

class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        kw.setdefault("fg_color", CARD)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", BORDER)
        super().__init__(master, **kw)

class Pill(ctk.CTkFrame):
    """Rounded status chip: colored dot + text."""
    def __init__(self, master, text="", color=MUTED):
        super().__init__(master, fg_color=CARD_HI, corner_radius=999, border_width=1, border_color=BORDER)
        self.dot = ctk.CTkLabel(self, text="", image=icon("dot", 14, color), width=14)
        self.dot.pack(side="left", padx=(10, 2), pady=5)
        self.label = ctk.CTkLabel(self, text=text, font=font(12, "bold"), text_color=TEXT)
        self.label.pack(side="left", padx=(0, 12), pady=5)

    def set(self, text, color):
        update(self.dot, image=icon("dot", 14, color))
        update(self.label, text=text)

class Badge(ctk.CTkLabel):
    def __init__(self, master, text, color=MUTED, **kw):
        super().__init__(master, text=text, font=font(11, "bold"), text_color=color, fg_color=CARD_HI,
                         corner_radius=6, height=20, padx=8, **kw)

class StatTile(Card):
    def __init__(self, master, title, icon_name, color=MUTED):
        super().__init__(master)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(top, text="", image=icon(icon_name, 16, color)).pack(side="left")
        ctk.CTkLabel(top, text=title.upper(), font=font(11, "bold"), text_color=MUTED).pack(side="left", padx=8)
        self.value = ctk.CTkLabel(self, text="-", font=font(24, "bold", display=True), anchor="w")
        self.value.pack(fill="x", padx=16, pady=(4, 0))
        self.sub = ctk.CTkLabel(self, text="", font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=140)
        self.sub.pack(fill="x", padx=16, pady=(0, 14))

    def set(self, value, sub=""):
        update(self.value, text=value)
        update(self.sub, text=sub)

def ghost_button(master, text, icon_name=None, command=None, **kw):
    """Secondary button: dark fill, subtle border."""
    kw.setdefault("height", 36)
    return ctk.CTkButton(master, text=text, image=icon(icon_name, 16) if icon_name else None, compound="left",
                         command=command, fg_color=CARD_HI, hover_color=BORDER, border_width=1, border_color=BORDER,
                         text_color=TEXT, font=font(13), **kw)

def section_title(master, text, sub=None):
    frame = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(frame, text=text, font=font(15, "bold", display=True), anchor="w").pack(fill="x")
    if sub:
        ctk.CTkLabel(frame, text=sub, font=font(12), text_color=MUTED, anchor="w", justify="left").pack(fill="x")
    return frame
