# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import datetime, os, queue, threading, time
from .engine import (AUDIO_INFO, AUDIO_KBPS, CONTAINER_INFO, FFmpeg, Encoder, PRESET_INFO, audio_codec, build_audio_cmd,
                     build_mux_cmd, build_record_cmd, capture_region, resolve_container, target_size)
from .log import log
from .monitors import list_monitors, select_monitors

STARTUP_GRACE = 4       # seconds an ffmpeg process must survive to count as started
TIME_JUMP = 30          # a loop gap this long means the PC slept; start a fresh chunk

def today_folder(root):
    path = os.path.join(root, datetime.date.today().strftime("%Y-%m-%d"))
    os.makedirs(path, exist_ok=True)
    return path

def chunk_name(folder, stamp=None, suffix="", ext="mp4"):
    """A path that does not exist yet. Existing recordings are never reused or overwritten."""
    stamp = stamp or datetime.datetime.now().strftime("%H-%M-%S")
    path = os.path.join(folder, f"{stamp}{suffix}.{ext}")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{stamp}{suffix}-{n}.{ext}")
        n += 1
    return path

class Recorder:
    """Owns the ffmpeg processes. Every public method is thread-safe; work happens on the recorder thread."""

    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self.ff = FFmpeg(cfg["ffmpeg_path"])
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._listeners = []
        self._thread = None
        self.running = True
        self.wanted = False             # user wants to record
        self.user_paused = False
        self.auto_reason = None         # "idle" | "locked" | "private" | None
        self.encoders = []
        self.chunk_start = None
        self.preset_used = self.encoder = None
        self.container = "mp4"
        self.audio_codec = None         # "opus" | "aac" while desktop audio is on and this FFmpeg can record it
        self._audio_failed = False      # capture failure already logged
        self.monitors = []
        self.backends = {}              # monitor device -> "ddagrab" | "gdigrab"
        self.failures = 0
        self.retry_at = 0
        self.error = None
        self.ready = False

    # ---- public API -------------------------------------------------------
    def start_thread(self):
        self._thread = threading.Thread(target=self._loop, name="recorder", daemon=True)
        self._thread.start()

    def start(self):        self._q.put(("start",))
    def stop(self):         self._q.put(("stop",))
    def toggle(self):       self._q.put(("toggle",))
    def toggle_pause(self): self._q.put(("toggle_pause",))
    def set_auto_pause(self, reason): self._q.put(("auto", reason))
    def apply_config(self, cfg):      self._q.put(("config", dict(cfg)))

    def shutdown(self, timeout=30):
        """Finish open files cleanly and stop the thread."""
        self._q.put(("quit",))
        if self._thread:
            self._thread.join(timeout)

    def subscribe(self, fn):
        self._listeners.append(fn)

    def status(self):
        with self._lock:
            state = ("recording" if self.encoders else "starting") if self._should_record() else \
                    "paused" if self.wanted and self.user_paused else \
                    "auto_paused" if self.wanted and self.auto_reason else "idle"
            return {"state": state, "reason": self.auto_reason, "files": [e.path for e in self.encoders],
                    "pids": [pid for e in self.encoders for pid in (e.pids() if hasattr(e, "pids") else [])],
                    "chunk_start": self.chunk_start, "chunk_seconds": self.cfg["chunk_minutes"] * 60,
                    "preset": self.preset_used, "encoder": self.encoder, "container": self.container,
                    "audio": self._audio_status(), "backends": dict(self.backends),
                    "monitors": list(self.monitors), "fps": self.cfg["fps"], "error": self.error, "ready": self.ready,
                    "resolution": self._resolution()}

    # ---- recorder thread --------------------------------------------------
    def _should_record(self):
        return self.wanted and not self.user_paused and not self.auto_reason and self.ready

    def _resolution(self):
        if not self.monitors:
            return None
        _, (_, _, w, h) = capture_region(self.monitors[0], self.cfg["crop_taskbar"])
        return target_size(w, h, self.cfg["downscale_factor"])

    def _audio_status(self):
        if not self.audio_codec:
            return None
        cap = next((e.audio for e in self.encoders if getattr(e, "audio", None)), None)
        info = cap.status() if cap else {"device": None, "mode": "idle", "peak": 0.0}
        return {"codec": self.audio_codec, "kbps": AUDIO_KBPS[self.audio_codec][self.cfg["quality"]], **info}

    def _notify(self):
        for fn in list(self._listeners):
            try:
                fn(self.status())
            except Exception:
                pass

    def _prepare(self):
        """Find ffmpeg, pick the encoder and monitors. Mirrors the old INIT log lines."""
        log("INIT", "Initializing FrameKeepOS", "CYAN")
        self.ready = False
        if not self.ff.ffmpeg:
            self.error = "ffmpeg.exe not found. Run dependencies.bat (or tools/fetch_ffmpeg.py)."
            log("ERROR", self.error, "RED")
            self._notify()
            return
        log("INIT", f"ffmpeg detected: {self.ff.ffmpeg}", "GREEN")
        wanted = self.cfg["preset"]
        self.preset_used, self.encoder = self.ff.resolve(wanted)
        if not self.encoder:
            self.error = "No working video encoder found in this ffmpeg build."
            log("ERROR", self.error, "RED")
            self._notify()
            return
        name, codec, _ = PRESET_INFO[self.preset_used]
        if self.preset_used != wanted:
            log("INIT", f"{PRESET_INFO[wanted][0]} preset unavailable here, using {name} ({codec})", "YELLOW")
        log("INIT", f"Encoder: {name} ({codec}) via {self.encoder}", "GREEN")
        self.container = resolve_container(self.cfg["container"], self.encoder, self.ff.muxers())
        chosen = CONTAINER_INFO[self.cfg["container"]][0]
        if self.container != self.cfg["container"]:
            why = "this FFmpeg build can't write it" if resolve_container(self.cfg["container"], self.encoder) == self.cfg["container"] \
                else f"it can't hold {codec}"
            log("INIT", f"{chosen} unavailable ({why}), saving as {CONTAINER_INFO[self.container][0]} instead", "YELLOW")
        log("INIT", f"File format: {CONTAINER_INFO[self.container][0]} (.{self.container})", "GREEN")
        self._prepare_audio()
        self.backends = {}
        self._pick_monitors()
        if not self.monitors:
            self.error = "No monitors found"
            log("ERROR", self.error, "RED")
        else:
            self.error = None
            self.ready = True
            log("INIT", "Initialization complete. Ready to record", "CYAN")
        self._notify()

    def _prepare_audio(self):
        self.audio_codec, self._audio_failed = None, False
        if not self.cfg["audio"]:
            return
        codec = audio_codec(self.cfg["audio_codec"], self.encoder, self.container)
        if codec not in self.ff.audio_codecs():
            log("WARN", f"Desktop audio is on, but this FFmpeg can't record {AUDIO_INFO[codec][0]}. Recording video only "
                        "(run tools/fetch_ffmpeg.py to get the bundled FFmpeg).", "YELLOW")
            return
        self.audio_codec = codec
        log("INIT", f"Desktop audio: {AUDIO_INFO[codec][0]} {AUDIO_KBPS[codec][self.cfg['quality']]} kbps", "GREEN")

    def _start_audio(self):
        """A running desktop-audio capture for the next file, or None (video still records without it)."""
        try:
            from .audio import AudioTrack
            track = AudioTrack(build_audio_cmd(self.ff.ffmpeg, self.audio_codec, self.cfg["quality"]),
                               self.cfg["audio_exclude_apps"])
            track.start()
            return track
        except Exception as e:
            if not self._audio_failed:
                self._audio_failed = True
                log("WARN", f"Desktop audio unavailable ({e}). Recording video only.", "YELLOW")
            return None

    def _pick_monitors(self, quiet=False):
        """Re-read the display layout. Backends already forced to GDI stay on GDI."""
        self.monitors = select_monitors(self.cfg["monitors"], list_monitors())
        for m in self.monitors:
            backend = self.cfg["capture_backend"]
            if self.backends.get(m.device) == "gdigrab" or m.dxgi_output is None:
                backend = "gdigrab"
            elif backend == "auto":
                backend = "ddagrab"
            self.backends[m.device] = backend
            if not quiet:
                _, (_, _, w, h) = capture_region(m, self.cfg["crop_taskbar"])
                tw, th = target_size(w, h, self.cfg["downscale_factor"])
                primary = ", primary" if m.primary else ""
                log("INIT", f"Capturing Display {m.index} ({w}x{h}{primary}) → {tw}x{th} @ {self.cfg['fps']} FPS via {backend}", "GREEN")

    def _loop(self):
        self._prepare()
        last_tick = time.time()
        while self.running:
            try:
                self._handle(self._q.get(timeout=0.5))
                while True:
                    self._handle(self._q.get_nowait())
            except queue.Empty:
                pass
            now = time.time()
            jumped = now - last_tick > TIME_JUMP
            last_tick = now
            try:
                self._tick(now, jumped)
            except Exception as e:
                self.error = str(e)
                log("ERROR", f"Recorder error: {e}", "RED")
                self._close_chunk("Recording stopped after an error")
                self.retry_at = time.time() + 10
                self._notify()
        self._close_chunk("FrameKeepOS shutting down")

    def _handle(self, cmd):
        kind = cmd[0]
        if kind == "start" or (kind == "toggle" and not self.wanted):
            if not self.cfg["log_root"]:
                log("ERROR", "Choose a recordings folder first", "RED")
                return
            self.wanted, self.user_paused, self.failures, self.retry_at = True, False, 0, 0
            log("INIT", "Recording started", "CYAN")
            if self.auto_reason:
                log("PAUSED", f"Auto-paused ({self.auto_reason}), recording begins when that ends", "MAGENTA")
        elif kind in ("stop", "toggle"):
            self.wanted = self.user_paused = False
            self._close_chunk("Recording stopped")
        elif kind == "pause" or (kind == "toggle_pause" and self.wanted and not self.user_paused):
            if self.wanted:
                self.user_paused = True
                self._close_chunk("Recording paused")
        elif kind in ("resume", "toggle_pause"):
            if self.wanted and self.user_paused:
                self.user_paused = False
                log("INIT", "Recording resumed", "CYAN")
        elif kind == "auto":
            reason = cmd[1]
            if reason != self.auto_reason:
                self.auto_reason = reason
                if reason and self.wanted and self.encoders:
                    self._close_chunk(f"Auto-paused ({reason})", color="MAGENTA")
                elif reason and self.wanted:
                    log("PAUSED", f"Auto-paused ({reason})", "MAGENTA")
                elif self.wanted and not self.user_paused:
                    log("INIT", "Auto-pause ended, resuming", "CYAN")
        elif kind == "config":
            old, self.cfg = self.cfg, cmd[1]
            if self.cfg["ffmpeg_path"] != old["ffmpeg_path"]:
                self.ff = FFmpeg(self.cfg["ffmpeg_path"])
            recording = bool(self.encoders)
            self._close_chunk("Applying new settings")
            self._prepare()
            if recording:
                log("CONFIG", "New settings apply from this chunk on", "GREEN")
        elif kind == "quit":
            self.running = False
        self._notify()

    def _tick(self, now, jumped):
        if not self._should_record():
            return
        if not self.encoders:
            if now >= self.retry_at:
                self._open_chunk()
            return
        dead = [e for e in self.encoders if not e.alive()]
        if dead:
            self._on_encoder_death(dead, now)
        elif jumped:
            self._roll("Woke from sleep, starting a new chunk")
        elif now - self.chunk_start >= self.cfg["chunk_minutes"] * 60:
            self._roll("Recording finished")

    def _open_chunk(self):
        folder = today_folder(self.cfg["log_root"])
        stamp = datetime.datetime.now().strftime("%H-%M-%S")
        multi = len(self.monitors) > 1
        with self._lock:
            self.chunk_start = time.time()
        for i, m in enumerate(self.monitors):
            path = chunk_name(folder, stamp, f"_mon{m.index}" if multi else "", self.container)
            # desktop audio goes in the first display's file only: one copy of the sound per chunk
            track = self._start_audio() if self.audio_codec and i == 0 else None
            cmd = build_record_cmd(self.ff.ffmpeg, self.cfg, m, self.encoder, self.backends[m.device], path,
                                   piped_start=track.start_time if track else None)
            mux = build_mux_cmd(self.ff.ffmpeg, track.path, path, self.encoder) if track else None
            enc = Encoder(cmd, path, m.device, mux, track) if track else Encoder(cmd, path, m.device)
            try:
                enc.start()
            except Exception:
                if track:
                    track.stop()
                    track.close()
                raise
            with self._lock:
                self.encoders = self.encoders + [enc]  # tracked as soon as it runs, so an error still closes it
            log("RECORDING", path, "CYAN")

    def _close_chunk(self, message, color="YELLOW"):
        if not self.encoders:
            return
        encoders = self.encoders
        for e in encoders:
            e.request_stop()  # stop every display's capture at once (matters for private-app pauses)
        for e in encoders:
            e.stop()
        with self._lock:
            self.encoders = []
            self.chunk_start = None
        log("STOPPED", message, color)
        self._notify()

    def _roll(self, message):
        self._close_chunk(message)
        self._open_chunk()

    def _on_encoder_death(self, dead, now):
        early = now - self.chunk_start < STARTUP_GRACE
        for e in dead:
            log("ERROR", f"ffmpeg stopped unexpectedly ({e.label}): {e.error_summary()}", "RED")
            if early and getattr(e, "audio", None) and self.audio_codec:
                # video must never stop because of audio: retry without it (until settings change or restart)
                self.audio_codec = None
                log("WARN", "Recording again without desktop audio", "YELLOW")
            # GPU capture can fail on some setups (secure desktop, driver resets); GDI capture always works
            elif early and self.backends.get(e.label) == "ddagrab":
                self.backends[e.label] = "gdigrab"
                log("WARN", f"Switching {e.label} to GDI capture", "YELLOW")
        self._close_chunk("Restarting recording")
        self._pick_monitors(quiet=True)  # a resolution or display change is the usual cause
        self.failures += 1
        delay = min(60, 2 ** min(self.failures, 6)) if early else 1
        if not early:
            self.failures = 0
        self.retry_at = now + delay
        self.error = dead[0].error_summary()
        self._notify()
