# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import os, threading
from PIL import Image, ImageDraw
from puretech_tray import SEPARATOR, Item, Menu, Tray as NativeTray
from .paths import ICON_PATH

NAME = "FrameKeep"            # shown in the tooltip, toasts and Start menu (APP_NAME stays the internal id)
APP_ID = "PureTech.FrameKeep"   # toasts are shown under this id (registered per user on the first notification)

STATE_COLORS = {"recording": "#FF4D5E", "starting": "#FF4D5E", "paused": "#FBBF24", "auto_paused": "#A78BFA", "idle": "#8C93A1"}
STATE_TEXT = {"recording": "Recording", "starting": "Starting…", "paused": "Paused", "auto_paused": "Auto-paused", "idle": "Ready"}
CAMERA_PROBLEMS = ("error", "offline", "busy")

def current_icon(state="idle", motion=False, problem=False):
    """The tray icon: the recorder state as the center dot, plus a blue corner dot while a camera sees motion and
    an orange badge when something needs attention (a camera that can't record, or disk space past the warning)."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill="#0E1014", outline="#2A2F38", width=3)
    d.ellipse((18, 18, 46, 46), fill=STATE_COLORS.get(state, STATE_COLORS["idle"]))
    if motion:
        d.ellipse((42, 0, 64, 22), fill="#60A5FA", outline="#0E1014", width=3)
    if problem:
        d.ellipse((40, 40, 64, 64), fill="#F97316", outline="#0E1014", width=3)
        d.rectangle((50, 45, 53, 54), fill="#0E1014")
        d.rectangle((50, 57, 53, 60), fill="#0E1014")
    return img

class Tray:
    def __init__(self, app):
        self.app = app
        self.state = "idle"
        self.reason = ""
        self._look = None
        self._lock = threading.Lock()
        self.icon = NativeTray(current_icon(), NAME, Menu(
            Item("Open FrameKeep", lambda: app.show_window(), default=True),
            Item("Open player", lambda: app.open_player()),
            SEPARATOR,
            Item(lambda: "Stop Recording" if self.state != "idle" else "Start Recording", lambda: app.recorder.toggle()),
            Item(lambda: "Resume Recording" if self.state == "paused" else "Pause Recording", lambda: app.recorder.toggle_pause(),
                 enabled=lambda: self.state != "idle"),
            Item("Cameras", Menu(self._camera_items), visible=lambda: bool(app.station.recorders)),
            SEPARATOR,
            Item("Quit", lambda: app.quit()),
        ), app_id=APP_ID, app_name=NAME, app_icon=ICON_PATH if os.path.isfile(ICON_PATH) else None)

    def _camera_items(self):
        """One checkbox per camera (ticked = recording, untick to pause it and free the device), then pause/resume
        all. Cameras run separately from desktop recording."""
        station = self.app.station
        items = [Item(rec.cam["name"], lambda r=rec: station.toggle(r.cam["id"]),
                      checked=lambda r=rec: not r.user_paused, enabled=lambda r=rec: r.cam["enabled"])
                 for rec in list(station.recorders.values())]
        items.append(SEPARATOR)
        items.append(Item(lambda: "Resume all cameras" if station.any_paused() else "Pause all cameras",
                          lambda: station.pause_all(not station.any_paused())))
        return items

    def refresh_cameras(self):
        self.icon.refresh_menu()
        self.update()

    def start(self):
        self.icon.start()

    def update(self, status=None):
        """Redraw the icon and tooltip from the recorder status (when given) and the cameras' current state."""
        with self._lock:
            if status is not None:
                self.state = status["state"]
                self.reason = status["reason"] if status["state"] == "auto_paused" else ""
            cams = self.app.station.status()
            motion = [c["name"] for c in cams if c["motion"]]
            problem = any(c["state"] in CAMERA_PROBLEMS for c in cams) or self.app.storage["over"]
            parts = [STATE_TEXT[self.state] + (f" ({self.reason})" if self.reason else "")]
            recording = sum(c["state"] == "recording" for c in cams)
            if cams:
                parts.append(f"{recording} of {len(cams)} cameras" if recording != len(cams) else
                             f"{len(cams)} camera{'s' if len(cams) != 1 else ''}")
            if motion:
                parts.append("motion: " + ", ".join(motion))
            look = (self.state, bool(motion), problem)
            new_icon = current_icon(*look) if look != self._look else None
            self._look = look
            self.icon.update(icon=new_icon, tooltip=f"{NAME}: " + "  ·  ".join(parts))

    def notify(self, text, title=NAME, actions=(), on_click=None):
        """A Windows notification. actions: (label, fn) buttons, run on the tray thread when clicked."""
        try:
            self.icon.notify(title, text, actions=actions, on_click=on_click)
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
