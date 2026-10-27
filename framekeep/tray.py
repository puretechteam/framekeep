# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
from . import APP_NAME

STATE_COLORS = {"recording": "#FF4D5E", "starting": "#FF4D5E", "paused": "#FBBF24", "auto_paused": "#A78BFA", "idle": "#8C93A1"}
STATE_TEXT = {"recording": "Recording", "starting": "Starting…", "paused": "Paused", "auto_paused": "Auto-paused", "idle": "Ready"}

def current_icon(state="idle"):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill="#0E1014", outline="#2A2F38", width=3)
    d.ellipse((18, 18, 46, 46), fill=STATE_COLORS.get(state, STATE_COLORS["idle"]))
    return img

class Tray:
    def __init__(self, app):
        self.app = app
        self.state = "idle"
        self.icon = pystray.Icon(APP_NAME, current_icon(), APP_NAME, pystray.Menu(
            item("Open FrameKeep", lambda: app.show_window(), default=True),
            pystray.Menu.SEPARATOR,
            item(lambda _: "Stop Recording" if self.state not in ("idle",) else "Start Recording", lambda: app.recorder.toggle()),
            item(lambda _: "Resume Recording" if self.state == "paused" else "Pause Recording", lambda: app.recorder.toggle_pause(),
                 enabled=lambda _: self.state != "idle"),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: app.quit()),
        ))

    def start(self):
        self.icon.run_detached()

    def update(self, status):
        reason = f" ({status['reason']})" if status["state"] == "auto_paused" and status["reason"] else ""
        if status["state"] != self.state:
            self.state = status["state"]
            self.icon.icon = current_icon(self.state)
            self.icon.update_menu()
        self.icon.title = f"{APP_NAME}: {STATE_TEXT[self.state]}{reason}"

    def notify(self, message):
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
