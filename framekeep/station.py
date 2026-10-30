# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""The camera station: one CameraRecorder per enabled camera, each on its own thread with its own retries, so an
unplugged webcam or a dropped network camera never touches the desktop recorder or the other cameras.

Cameras run independently of desktop recording: they keep recording while the PC is locked or idle, or a private
app is open. A camera pauses only when the user pauses it (tray, Cameras page, camera hotkey) or while one of its
"pause while these apps are open" programs runs. Pausing ends FFmpeg cleanly ('q'), which releases the device so a
video call can use the webcam."""

import datetime, os, threading, time
from . import audio as audio_mod, cameras
from .config import camera_root
from .engine import (ANALYSIS_H, ANALYSIS_W, Encoder, FFmpeg, audio_codec, build_camera_cmd, camera_copies,
                     resolve_container)
from .log import log
from .motion import EventLog, MotionDetector, read_events

FRAME_BYTES = ANALYSIS_W * ANALYSIS_H * 3
APP_CHECK = 2.0              # seconds between checks of the "pause while these apps are open" lists
NOTIFY_GAP = 60              # at most one motion notification per camera per minute
BUSY_HINTS = ("already in use", "could not run graph", "device is busy", "0x800700aa")
# Windows' C runtime numbers: -138 ETIMEDOUT, -107 ECONNREFUSED, -116 ENETUNREACH, -113 EHOSTUNREACH
OFFLINE_HINTS = ("could not find", "no such file", "connection", "timed out", "timeout", "i/o error",
                 "network is unreachable", "host", "refused", "404", "end of file", "error number -138",
                 "error number -107", "error number -116", "error number -113")
LOGIN_HINTS = ("401", "unauthorized", "403", "forbidden")

def camera_preset(cam, cfg):
    """The format a camera records in: its own, or the desktop's, except H.266, which can't keep up with camera
    video (docs/perf), so Ultra means AV1 for cameras."""
    preset = cfg["preset"] if cam["preset"] == "desktop" else cam["preset"]
    return "smallest" if preset == "ultra" else preset

class CameraRecorder:
    """One camera's FFmpeg, restarted with backoff when it dies, plus its motion detector and live frame."""

    def __init__(self, cam, station):
        self.cam, self.station = dict(cam), station
        self.user_paused = False
        self.app_paused = None            # program name while paused for an app
        self.enc = None
        self.state = "off"                # off | starting | recording | paused | app_paused | busy | offline | error
        self.error = None
        self.failures = 0
        self.retry_at = 0.0
        self.started = None
        self.encoder = None               # video encoder, or None while copying the camera's stream
        self.audio = None
        self.latest = None                # newest analysis frame (RGB bytes) and its time
        self.latest_at = 0.0
        self.frames = 0
        self.events_today = 0
        self.motion_on = False
        self._last_notify = 0.0
        self._wake = threading.Event()
        self._quit = False
        self._lock = threading.Lock()
        self.log = EventLog(self.folder())
        self.detector = self._detector()
        self._day = None
        self._count_today()
        self._thread = threading.Thread(target=self._loop, name=f"camera-{cam['id']}", daemon=True)

    # ---- control (any thread) ----------------------------------------------------------------------------
    def start_thread(self):
        self._thread.start()

    def set_paused(self, paused):
        self.user_paused = bool(paused)
        self.retry_at = 0
        self._wake.set()

    def set_app_paused(self, name):
        if name != self.app_paused:
            self.app_paused = name
            self.retry_at = 0
            self._wake.set()

    def quit(self, wait=True):
        self._quit = True
        self._wake.set()
        if wait and self._thread.is_alive():
            self._thread.join(20)

    # ---- info ---------------------------------------------------------------------------------------------
    def folder(self):
        root = camera_root(self.station.cfg)
        return os.path.join(root, self.cam["folder"]) if root else ""

    def label(self):
        return self.cam["name"]

    def current_file(self):
        """The chunk being written now (newest file of today's folder), or None."""
        if not self.enc or self.state != "recording":
            return None
        day = os.path.join(self.folder(), datetime.date.today().strftime("%Y-%m-%d"))
        try:
            files = [e for e in os.scandir(day) if e.is_file() and not e.name.endswith(".jsonl")]
        except OSError:
            return None
        return max(files, key=lambda e: e.name).path if files else None

    def status(self):
        return {"id": self.cam["id"], "name": self.cam["name"], "state": self.state, "error": self.error,
                "since": self.started, "encoder": self.encoder, "audio": self.audio, "motion": self.motion_on,
                "level": self.detector.level if self.detector else 0.0, "events_today": self.events_today,
                "pids": self.enc.pids() if self.enc and self.enc.proc else [], "app": self.app_paused,
                "file": self.current_file(), "kind": self.cam["kind"], "copy": camera_copies(self.cam)}

    def frame(self):
        """(rgb bytes, width, height, time) of the newest live frame, or None."""
        with self._lock:
            return (self.latest, ANALYSIS_W, ANALYSIS_H, self.latest_at) if self.latest else None

    # ---- thread ---------------------------------------------------------------------------------------------
    def _wanted(self):
        return (self.cam["enabled"] and not self.user_paused and not self.app_paused and not self._quit
                and bool(self.folder()))

    def _loop(self):
        while True:
            self._wake.wait(0.5)
            self._wake.clear()
            if self._quit:
                self._stop()
                return
            try:
                self._tick(time.time())
            except Exception as e:          # never let one camera take the station down
                log("ERROR", f"Camera {self.label()}: {cameras.mask(e)}", "RED")
                self._stop()
                self.state, self.error = "error", cameras.mask(e)
                self.retry_at = time.time() + 30

    def _count_today(self):
        """Motion events so far today (re-read at midnight, and at start so earlier runs count too)."""
        self._day = datetime.date.today()
        today = os.path.join(self.folder(), self._day.strftime("%Y-%m-%d")) if self.folder() else ""
        self.events_today = len(read_events(today)) if today else 0

    def _tick(self, now):
        if datetime.date.today() != self._day:
            self._count_today()
        if not self._wanted():
            if self.enc:
                self._stop()
            self.state = ("app_paused" if self.app_paused else "paused") if (self.user_paused or self.app_paused) else \
                         ("off" if not self.cam["enabled"] else self.state if self.state == "error" else "off")
            return
        if self.enc and not self.enc.alive():
            self._died(now)
        elif self.enc:
            if self.state != "recording" and self.frames:     # pictures are flowing: the camera really works
                self.state, self.failures, self.error = "recording", 0, None
                log("CAMERA", f"{self.cam['name']}: recording", "CYAN")
                self.station.changed()
            self._prepare_days(now)
        elif now >= self.retry_at:
            self._start(now)

    def _prepare_days(self, now):
        """The segment muxer can't create folders: make today's and, near midnight, tomorrow's."""
        for t in (now, now + 120):
            os.makedirs(os.path.join(self.folder(), time.strftime("%Y-%m-%d", time.localtime(t))), exist_ok=True)

    def _start(self, now):
        st, cam, cfg = self.station, self.cam, self.station.cfg
        ff = st.ff
        if not ff.ffmpeg:
            self.state, self.error = "error", "FFmpeg was not found"
            self.retry_at = now + 60
            return
        copy = camera_copies(cam)
        encoder, container = None, "mkv"
        if not copy:
            _, encoder = ff.resolve(camera_preset(cam, cfg))
            if not encoder:
                self.state, self.error = "error", "No working video encoder"
                self.retry_at = now + 60
                return
            container = resolve_container(cfg["container"], encoder, ff.muxers())
        sound = None
        if cam["audio"] and (cam["kind"] == "url" or cam["mic"]):
            sound = audio_codec(cfg["audio_codec"], encoder or "libx264", container)
            if sound not in ff.camera_audio_codecs():
                sound = None
        mode = None
        if cam["kind"] == "device":
            mode = st.mode_for(cam)
        self._prepare_days(now)
        self._unique_second()
        pattern = os.path.join(self.folder(), "%Y-%m-%d", f"%H-%M-%S.{container}")
        cmd = build_camera_cmd(ff.ffmpeg, cam, cfg, encoder, pattern,
                               cameras.input_args(cam, mode, audio=bool(sound)), audio=sound)
        self.detector = self._detector()
        self.frames = 0
        self.enc = Encoder(cmd, pattern, cam["name"], reader=self._read)
        try:
            self.enc.start()
        except OSError as e:
            self.enc = None
            self._failed(now, f"Could not start FFmpeg: {e}")
            return
        self.encoder, self.audio = encoder, sound
        self.started = datetime.datetime.now()
        if not self.failures:           # while retrying, keep showing why ("Not reachable") until pictures arrive
            self.state = "starting"
            log("CAMERA", f"{cam['name']}: starting ({'copying the stream' if copy else encoder}"
                          f"{', ' + sound if sound else ''})", "CYAN")
        self.station.changed()

    def _unique_second(self):
        """Chunk names are the second they start in. Never start in a second that already named a file."""
        stamp = lambda: os.path.join(self.folder(), time.strftime("%Y-%m-%d"), time.strftime("%H-%M-%S"))
        for _ in range(3):
            base = stamp()
            if not any(os.path.exists(f"{base}.{ext}") for ext in ("mp4", "mkv", "webm")):
                return
            time.sleep(1.05)

    def _died(self, now):
        text = self.enc.error_summary()
        early = self.state != "recording"   # never delivered a picture: back off further each time
        self.enc.stop(timeout=5)
        self.enc = None
        self._close_motion()
        self._failed(now, text, early)

    def _failed(self, now, text, early=True):
        lower = text.lower()
        secrets = [cameras.unprotect(self.cam.get("secret", ""))]
        if "unknown input format" in lower or "protocol not found" in lower:
            state, message = "error", "This FFmpeg can't read cameras. Run tools/fetch_ffmpeg.py for FrameKeep's own build."
        elif any(h in lower for h in BUSY_HINTS):
            state, message = "busy", "In use by another app"
        elif self.cam["kind"] == "url" and any(h in lower for h in LOGIN_HINTS):
            state, message = "error", "The camera refused the login. Check the user and password."
        elif any(h in lower for h in OFFLINE_HINTS):
            state, message = "offline", "Not reachable" if self.cam["kind"] == "url" else "Not connected"
        else:
            state, message = "error", cameras.mask(text, secrets)
        self.failures = self.failures + 1 if early else 1
        delay = min(60, 2 ** self.failures)
        if state != self.state or message != self.error or not early:
            detail = cameras.mask(text, secrets)
            log("CAMERA", f"{self.cam['name']}: {message}{f' ({detail})' if detail != message else ''}; "
                          f"retrying in {delay} s", "YELLOW")
        self.state, self.error = state, message
        self.retry_at = now + delay
        self.station.changed()

    def _stop(self):
        if self.enc:
            enc, self.enc = self.enc, None
            if self.frames:
                enc.stop()                  # 'q': FFmpeg finishes the file cleanly
            else:
                enc.stop(timeout=1)         # still connecting: nothing to finish, don't wait for a dead camera
            log("CAMERA", f"{self.cam['name']}: stopped", "CYAN")
        self._close_motion()
        self.station.changed()

    # ---- motion ---------------------------------------------------------------------------------------------
    def _detector(self):
        if not self.cam["motion"]:
            return None
        # a copied stream only decodes keyframes (a few seconds apart), so one changed frame starts an event
        start = 1 if camera_copies(self.cam) else 2
        return MotionDetector(ANALYSIS_W, ANALYSIS_H, self.cam["sensitivity"], start,
                              on_start=self._motion_start, on_end=self._motion_end)

    def _read(self, stream):
        """Analysis frames from FFmpeg's stdout: keep the newest for the live tile, feed motion detection."""
        detector = self.detector
        while True:
            data = stream.read(FRAME_BYTES)
            if not data or len(data) < FRAME_BYTES:
                return
            now = time.time()
            with self._lock:
                self.latest, self.latest_at = data, now
            self.frames += 1
            if detector:
                detector.feed(data, now)

    def _motion_start(self, t):
        self.motion_on = True
        self.station.changed()
        if self.cam["notify"] and t - self._last_notify >= NOTIFY_GAP:
            self._last_notify = t
            self.station.on_motion(self.cam, t)

    def _motion_end(self, start, end, peak):
        self.motion_on = False
        try:
            self.log.add(start, end, peak)
        except OSError as e:
            log("WARN", f"Could not save a motion event for {self.cam['name']}: {e}", "YELLOW")
        if time.strftime("%Y-%m-%d", time.localtime(start)) == time.strftime("%Y-%m-%d"):
            self.events_today += 1
        self.station.changed()

    def _close_motion(self):
        if self.detector:
            self.detector.close()

class Station:
    """Owns the camera recorders. Thread-safe; reconciles with the config whenever settings are saved."""

    def __init__(self, cfg, on_motion=None):
        self.cfg = dict(cfg)
        self.ff = FFmpeg(cfg["ffmpeg_path"])
        self.on_motion = on_motion or (lambda cam, t: None)   # a camera with notifications on saw motion
        self.recorders = {}               # camera id -> CameraRecorder
        self._modes = {}                  # (device, height, fps) -> chosen capture mode
        self._listeners = []
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._apps = threading.Thread(target=self._watch_apps, name="camera-apps", daemon=True)

    def start(self):
        self.apply_config(self.cfg)
        self._apps.start()

    def subscribe(self, fn):
        self._listeners.append(fn)

    def changed(self):
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def apply_config(self, cfg):
        """Start new cameras, restart changed ones, stop removed ones. Pauses survive a settings change."""
        old_cfg, self.cfg = self.cfg, dict(cfg)
        if cfg["ffmpeg_path"] != old_cfg["ffmpeg_path"]:
            self.ff = FFmpeg(cfg["ffmpeg_path"])
        shared = ("preset", "quality", "container", "chunk_minutes", "audio_codec", "log_root", "camera_root",
                  "ffmpeg_path")
        shared_changed = any(cfg.get(k) != old_cfg.get(k) for k in shared)
        wanted = {c["id"]: c for c in cfg["cameras"]}
        with self._lock:
            for cam_id in list(self.recorders):
                rec = self.recorders[cam_id]
                if cam_id not in wanted or wanted[cam_id] != rec.cam or shared_changed:
                    paused = rec.user_paused
                    rec.quit()
                    del self.recorders[cam_id]
                    if cam_id in wanted:
                        self._add(wanted[cam_id]).set_paused(paused)
            for cam_id, cam in wanted.items():
                if cam_id not in self.recorders:
                    self._add(cam)
        self.changed()

    def _add(self, cam):
        rec = CameraRecorder(cam, self)
        self.recorders[cam["id"]] = rec
        rec.start_thread()
        return rec

    def mode_for(self, cam):
        """The capture mode for a DirectShow camera (cached per device and size)."""
        key = (cam["device"], cam["height"], cam["fps"])
        if key not in self._modes:
            self._modes[key] = cameras.pick_mode(cameras.device_modes(self.ff, cam["device"]), cam["height"], cam["fps"])
        return self._modes[key]

    # ---- pausing -----------------------------------------------------------------------------------------
    def pause(self, cam_id, paused):
        rec = self.recorders.get(cam_id)
        if rec:
            rec.set_paused(paused)
            log("CAMERA", f"{rec.cam['name']}: {'paused' if paused else 'resumed'}", "CYAN")

    def toggle(self, cam_id):
        rec = self.recorders.get(cam_id)
        if rec:
            self.pause(cam_id, not rec.user_paused)

    def pause_all(self, paused):
        for cam_id in list(self.recorders):
            self.pause(cam_id, paused)

    def any_paused(self):
        return any(r.user_paused for r in self.recorders.values())

    def toggle_hotkey(self):
        """The camera hotkey: pause the cameras it controls, or resume them if they are all paused."""
        recs = [r for r in self.recorders.values() if r.cam["hotkey"] and r.cam["enabled"]]
        if recs:
            pause = not all(r.user_paused for r in recs)
            for r in recs:
                self.pause(r.cam["id"], pause)

    def _watch_apps(self):
        while not self._quit.wait(APP_CHECK):
            recs = [r for r in list(self.recorders.values()) if r.cam["pause_apps"]]
            if not recs:
                continue
            try:
                running = {exe for _, _, exe in audio_mod.processes()}
            except OSError:
                continue
            for r in recs:
                r.set_app_paused(next((a for a in r.cam["pause_apps"] if a in running), None))

    # ---- info and shutdown -------------------------------------------------------------------------------
    def status(self):
        return [r.status() for r in list(self.recorders.values())]

    def active_files(self):
        return [f for f in (r.current_file() for r in list(self.recorders.values())) if f]

    def pids(self):
        return [p for r in list(self.recorders.values()) for p in (r.enc.pids() if r.enc and r.enc.proc else [])]

    def shutdown(self):
        self._quit.set()
        recs = list(self.recorders.values())
        for r in recs:
            r.quit(wait=False)
        for r in recs:
            r.quit()
