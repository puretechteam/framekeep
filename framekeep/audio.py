# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Desktop audio: WASAPI loopback capture through ctypes, encoded by a small FFmpeg and muxed into the recording.

FFmpeg has no way to record what Windows is playing (dshow needs a "Stereo Mix" device most PCs don't have), so
FrameKeep captures it and hands an FFmpeg 48 kHz float stereo on stdin (AudioTrack explains why that FFmpeg is
separate from the video's). No NumPy or audio library: one thread wakes 10 times a second and copies bytes.

The timeline is continuous: loopback delivers nothing while nothing plays, so silence is written to keep audio
as long as the wall-clock time (and in sync with the video). Each packet is placed by its capture timestamp, so a
gap (device switch, nothing playing) becomes silence and the two clocks can't drift apart."""

import array, collections, ctypes, os, subprocess, sys, threading, time
from ctypes import POINTER, byref, c_int, c_uint32, c_uint64, c_void_p, wintypes
from .log import log
from .monitors import GUID, _com, _release

RATE, CHANNELS = 48000, 2
FRAME_BYTES = 4 * CHANNELS                  # float32 per channel
TICK = 0.1                                  # seconds between buffer reads
BUFFER_100NS = 10_000_000                   # 1 s WASAPI buffer: plenty of slack for a 0.1 s poll
HOLD_BACK = 0.25                            # silence is written up to this far behind now, so late packets still fit
TOLERANCE = RATE // 50                      # 20 ms: placement error corrected by padding or skipping
DEVICE_CHECK = 0.5                          # seconds between default-device checks
EXCLUDE_CHECK = 2.0                         # seconds between looks for excluded apps
PROCESS_LOOPBACK_BUILD = 20348              # first Windows build with per-process loopback (Windows 11)

S_OK = 0
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0
STREAMFLAGS_LOOPBACK, STREAMFLAGS_EVENTCALLBACK = 0x00020000, 0x00040000
STREAMFLAGS_AUTOCONVERTPCM, STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x80000000, 0x08000000
BUFFERFLAGS_SILENT, BUFFERFLAGS_TIMESTAMP_ERROR = 0x2, 0x4
WAVE_FORMAT_IEEE_FLOAT = 3
PIPE_ACCESS_OUTBOUND, PIPE_TYPE_BYTE, PIPE_WAIT = 0x2, 0x0, 0x0
GENERIC_READ, OPEN_EXISTING = 0x80000000, 3
ERROR_PIPE_CONNECTED = 535
INVALID_HANDLE = wintypes.HANDLE(-1).value
VT_LPWSTR, VT_BLOB = 31, 65
SESSION_ACTIVE = 1
NO_WINDOW = 0x08000000
TH32CS_SNAPPROCESS = 0x2

def guid(text):
    h = text.replace("-", "")
    return GUID(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16), (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))

CLSID_MMDeviceEnumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
IID_IAudioSessionManager2 = guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_IUnknown = guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
IID_IActivateAudioInterfaceCompletionHandler = guid("41D949AB-9862-444A-80F6-C261334DA5EB")

class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]

class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("p1", c_void_p), ("p2", c_void_p)]   # p1 is pwszVal, or (cbSize, pBlobData) for a blob

class PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", c_int), ("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", c_int)]

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]

PKEY_Device_FriendlyName = PROPERTYKEY(guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)

if sys.platform == "win32":
    # own instance: last-error is kept per call, and the argtypes set here don't change other modules' kernel32
    kernel32, ole32 = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.windll.ole32
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.DWORD, wintypes.DWORD, c_void_p]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, c_void_p, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.HANDLE]
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, c_void_p]
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD), c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, c_void_p]
    ole32.CoTaskMemFree.argtypes = [c_void_p]

def qpc_100ns():
    """Now on the clock WASAPI stamps packets with (QueryPerformanceCounter in 100 ns units)."""
    count, freq = ctypes.c_int64(), ctypes.c_int64()
    kernel32.QueryPerformanceCounter(byref(count))
    kernel32.QueryPerformanceFrequency(byref(freq))
    return count.value * 10_000_000 // freq.value

def windows_build():
    return sys.getwindowsversion().build if sys.platform == "win32" else 0

def process_loopback_supported():
    return windows_build() >= PROCESS_LOOPBACK_BUILD

def float_format():
    return WAVEFORMATEX(WAVE_FORMAT_IEEE_FLOAT, CHANNELS, RATE, RATE * FRAME_BYTES, FRAME_BYTES, 32, 0)

# ---- excluded apps --------------------------------------------------------------------------------------------
def processes():
    """[(pid, parent pid, exe name lowercased)] for every running process."""
    out = []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower()))
            ok = kernel32.Process32NextW(snap, byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out

def excluded_roots(procs, names):
    """pids of the top process of each running excluded app (Discord, Teams and browsers spawn child processes
    with the same name; excluding the top one's process tree covers them all)."""
    if not names:
        return []
    by_pid = {pid: (ppid, exe) for pid, ppid, exe in procs}
    roots = []
    for pid, ppid, exe in procs:
        if exe in names and by_pid.get(ppid, (0, ""))[1] != exe:
            roots.append(pid)
    return roots

# ---- the pacing rule, kept free of Windows calls so it can be tested ----------------------------------------
class Timeline:
    """Turns packets stamped with capture times into one gap-free stream that tracks the wall clock."""
    def __init__(self, t0):
        self.t0 = t0            # 100 ns clock time of frame 0
        self.written = 0        # frames handed out so far

    def frame_at(self, t):
        return (t - self.t0) * RATE // 10_000_000

    def place(self, frames, stamp):
        """(silent frames to write first, frames of this packet to skip) for a packet starting at stamp
        (or None when the device gave no usable timestamp)."""
        if stamp is None:
            return 0, 0
        start = self.frame_at(stamp)
        gap = start - self.written
        if gap > TOLERANCE:
            return gap, 0
        if gap < -TOLERANCE:
            return 0, min(frames, -gap)
        return 0, 0

    def fill(self, now):
        """Silent frames needed to bring the stream to HOLD_BACK behind now."""
        return max(0, self.frame_at(now) - int(HOLD_BACK * RATE) - self.written)

# ---- COM plumbing --------------------------------------------------------------------------------------------
def _hr(value):
    if value < 0:
        raise OSError(f"HRESULT 0x{value & 0xFFFFFFFF:08X}")
    return value

def _device_id(device):
    ptr = ctypes.c_wchar_p()
    dev_id = ""
    if _com(device, 5, POINTER(ctypes.c_wchar_p))(byref(ptr)) == S_OK and ptr:
        dev_id = ptr.value
        ole32.CoTaskMemFree(ptr)
    return dev_id

def _device_info(device):
    """(endpoint id, friendly name) of an IMMDevice."""
    dev_id = _device_id(device)
    name = "Sound output"
    store = c_void_p()
    if _com(device, 4, wintypes.DWORD, POINTER(c_void_p))(0, byref(store)) == S_OK:
        value = PROPVARIANT()
        if _com(store, 5, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(byref(PKEY_Device_FriendlyName), byref(value)) == S_OK:
            if value.vt == VT_LPWSTR and value.p1:
                name = ctypes.wstring_at(value.p1)
            ole32.PropVariantClear(byref(value))
        _release(store)
    return dev_id, name

QI = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
REF = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)
COMPLETED = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p)

class _CompletionHandler:
    """A minimal IActivateAudioInterfaceCompletionHandler (also IAgileObject, which the API requires)."""
    def __init__(self):
        self.done = threading.Event()
        wanted = [bytes(i) for i in (IID_IUnknown, IID_IAgileObject, IID_IActivateAudioInterfaceCompletionHandler)]

        def query(this, riid, out):
            if bytes(riid.contents) in wanted:
                out[0] = this
                return S_OK
            out[0] = None
            return 0x80004002 - (1 << 32)      # E_NOINTERFACE

        def completed(this, op):
            self.done.set()
            return S_OK

        self._fns = [QI(query), REF(lambda this: 1), REF(lambda this: 1), COMPLETED(completed)]
        self._vtbl = (c_void_p * 4)(*[ctypes.cast(f, c_void_p) for f in self._fns])
        self._obj = (c_void_p * 1)(ctypes.addressof(self._vtbl))
        self.ptr = c_void_p(ctypes.addressof(self._obj))

class Source:
    """One open WASAPI loopback stream: the default output device, or everything except one app's process tree."""
    def __init__(self, client, name, dev_id=""):
        self.client, self.name, self.dev_id = client, name, dev_id
        self.capture = c_void_p()
        _hr(_com(client, 14, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioCaptureClient), byref(self.capture)))
        _hr(_com(client, 10)())
        self._next = _com(self.capture, 5, POINTER(c_uint32))
        self._get = _com(self.capture, 3, POINTER(c_void_p), POINTER(c_uint32), POINTER(wintypes.DWORD),
                         POINTER(c_uint64), POINTER(c_uint64))
        self._free = _com(self.capture, 4, c_uint32)

    @staticmethod
    def _init(client, flags):
        fmt = float_format()
        _hr(_com(client, 3, c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, POINTER(WAVEFORMATEX), c_void_p)(
            0, flags, BUFFER_100NS, 0, byref(fmt), None))

    @classmethod
    def endpoint(cls, enumerator):
        """Loopback of the current default output device."""
        device = c_void_p()
        _hr(_com(enumerator, 4, c_int, c_int, POINTER(c_void_p))(0, 0, byref(device)))   # eRender, eConsole
        try:
            dev_id, name = _device_info(device)
            client = c_void_p()
            _hr(_com(device, 3, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(
                byref(IID_IAudioClient), CLSCTX_ALL, None, byref(client)))
        finally:
            _release(device)
        try:
            cls._init(client, STREAMFLAGS_LOOPBACK | STREAMFLAGS_AUTOCONVERTPCM | STREAMFLAGS_SRC_DEFAULT_QUALITY)
            return cls(client, name, dev_id)
        except OSError:
            _release(client)
            raise

    @classmethod
    def excluding(cls, pid, label):
        """Every app's sound except process pid and its children (Windows 11's process loopback)."""
        params = PROCESS_LOOPBACK_PARAMS(1, pid, 1)    # PROCESS_LOOPBACK, EXCLUDE_TARGET_PROCESS_TREE
        blob = PROPVARIANT(vt=VT_BLOB, p1=ctypes.sizeof(params), p2=ctypes.addressof(params))
        handler, op = _CompletionHandler(), c_void_p()
        activate = ctypes.windll.mmdevapi.ActivateAudioInterfaceAsync
        activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
        _hr(activate("VAD\\Process_Loopback", byref(IID_IAudioClient), byref(blob), handler.ptr, byref(op)))
        try:
            if not handler.done.wait(5):
                raise OSError("process loopback activation timed out")
            result, client = ctypes.c_long(), c_void_p()
            _hr(_com(op, 3, POINTER(ctypes.c_long), POINTER(c_void_p))(byref(result), byref(client)))
            _hr(result.value)
        finally:
            _release(op)
        try:
            # process loopback buffers only 10 ms whatever is asked for, so it's read on its event (see _loop)
            cls._init(client, STREAMFLAGS_LOOPBACK | STREAMFLAGS_EVENTCALLBACK | STREAMFLAGS_AUTOCONVERTPCM)
            src = cls.__new__(cls)
            src._event = kernel32.CreateEventW(None, False, False, None)
            _hr(_com(client, 13, wintypes.HANDLE)(src._event))
            cls.__init__(src, client, f"All apps except {label}")
            return src
        except OSError:
            _release(client)
            raise

    def packets(self):
        """Yield (bytes or None for silence, frames, capture time or None) for everything buffered."""
        size, data, frames, flags = c_uint32(), c_void_p(), c_uint32(), wintypes.DWORD()
        pos, stamp = c_uint64(), c_uint64()
        while True:
            _hr(self._next(byref(size)))
            if not size.value:
                return
            _hr(self._get(byref(data), byref(frames), byref(flags), byref(pos), byref(stamp)))
            n = frames.value
            try:
                silent = flags.value & BUFFERFLAGS_SILENT or not data
                raw = None if silent else ctypes.string_at(data, n * FRAME_BYTES)
                good = stamp.value and not flags.value & BUFFERFLAGS_TIMESTAMP_ERROR
            finally:
                self._free(n)
            yield raw, n, stamp.value if good else None

    def close(self):
        try:
            _com(self.client, 11)()
        except OSError:
            pass
        _release(self.capture)
        _release(self.client)
        event = getattr(self, "_event", None)
        if event:
            kernel32.CloseHandle(event)

def active_session_pids(enumerator):
    """pids with an audio session playing on the default output device right now."""
    pids = set()
    device, manager, sessions = c_void_p(), c_void_p(), c_void_p()
    if _com(enumerator, 4, c_int, c_int, POINTER(c_void_p))(0, 0, byref(device)) != S_OK:
        return pids
    try:
        if _com(device, 3, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(
                byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(manager)) != S_OK:
            return pids
        if _com(manager, 5, POINTER(c_void_p))(byref(sessions)) != S_OK:
            return pids
        count = c_int()
        _com(sessions, 3, POINTER(c_int))(byref(count))
        for i in range(count.value):
            control, control2 = c_void_p(), c_void_p()
            if _com(sessions, 4, c_int, POINTER(c_void_p))(i, byref(control)) != S_OK:
                continue
            state, pid = c_int(), wintypes.DWORD()
            if _com(control, 3, POINTER(c_int))(byref(state)) == S_OK and state.value == SESSION_ACTIVE and \
               _com(control, 0, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioSessionControl2), byref(control2)) == S_OK:
                if _com(control2, 14, POINTER(wintypes.DWORD))(byref(pid)) == S_OK and pid.value:
                    pids.add(pid.value)
                _release(control2)
            _release(control)
    finally:
        _release(sessions)
        _release(manager)
        _release(device)
    return pids

# ---- the capture -----------------------------------------------------------------------------------------------
class LoopbackCapture:
    """WASAPI loopback written as one gap-free 48 kHz float stereo stream to write(bytes), from t0 (a qpc_100ns()
    time) until stop(). Follows the default output device and the exclude list."""
    def __init__(self, write, t0, exclude=(), on_done=lambda: None):
        self.write_raw, self.on_done = write, on_done
        self.exclude = {e.lower() for e in exclude}
        self.device = None          # what's being captured, for the dashboard
        self.mode = "starting"      # "device" | "excluding" | "no device" | "stopped"
        self.muted = False          # an excluded app is playing and can't be filtered out
        self.peak = 0.0             # recent peak level, 0..1
        self.error = None
        self.timeline = Timeline(t0)
        self._end = None            # timeline frame to stop at
        self._next_meter = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._zeros = bytes(int(RATE * TICK) * FRAME_BYTES)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="audio-capture", daemon=True)
        self._thread.start()

    def finish(self):
        """End the stream now: what's buffered up to now is still written, nothing after. Returns at once."""
        if self._end is None:
            self._end = self.timeline.frame_at(qpc_100ns())
        self._stop.set()

    def stop(self, timeout=5):
        self.finish()
        if self._thread:
            self._thread.join(timeout)
        self.mode = "stopped"

    def status(self):
        return {"device": self.device, "mode": "muted" if self.muted else self.mode,
                "peak": 0.0 if self.muted else round(self.peak, 3)}

    # ---- capture thread ----
    def _write(self, data, frames):
        if self._end is not None:
            frames = max(0, min(frames, self._end - self.timeline.written))
            data = data[:frames * FRAME_BYTES]
        if frames:
            self.write_raw(data)
            self.timeline.written += frames

    def _silence(self, frames):
        while frames > 0:
            n = min(frames, len(self._zeros) // FRAME_BYTES)
            self._write(self._zeros[:n * FRAME_BYTES], n)
            frames -= n

    def _measure(self, raw):
        """Peak of a short slice of the latest packet (cheap enough to run twice a second)."""
        samples = array.array("f", raw[:960 * FRAME_BYTES])
        self.peak = max(max(samples, default=0.0), -min(samples, default=0.0), 0.0)

    def _run(self):
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        enumerator, source = c_void_p(), None
        try:
            _hr(ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL, byref(IID_IMMDeviceEnumerator),
                                       byref(enumerator)))
            source = self._loop(enumerator)
            if self._end is not None:
                self._drain(source)
                self._silence(self._end - self.timeline.written)
        except BrokenPipeError:
            pass                    # the encoder exited (FFmpeg error, or FrameKeep closing)
        except Exception as e:
            self.error = str(e)
            log("WARN", f"Desktop audio stopped: {e}", "YELLOW")
        finally:
            if source:
                source.close()
            _release(enumerator)
            ole32.CoUninitialize()
            self.on_done()

    def _open(self, enumerator, exclude_pid=None, label=""):
        """The stream to read now, or None (no output device: the file gets silence until one appears)."""
        try:
            if exclude_pid:
                src = Source.excluding(exclude_pid, label)
                self.mode = "excluding"
            else:
                src = Source.endpoint(enumerator)
                self.mode = "device"
            self.device = src.name
            return src
        except OSError as e:
            if self.mode != "no device":
                log("WARN", f"No sound output to record ({e}); writing silence until one is available", "YELLOW")
            self.mode, self.device = "no device", None
            return None

    def _drain(self, source, now=None):
        """Write everything the stream has buffered. True if there was anything."""
        got = False
        if not source:
            return got
        for raw, frames, stamp in source.packets():
            got = True
            pad, skip = self.timeline.place(frames, stamp)
            self._silence(pad)
            if skip >= frames:
                continue
            if raw is None or self.muted:
                self._silence(frames - skip)
            else:
                self._write(raw[skip * FRAME_BYTES:], frames - skip)
                if now is not None and now >= self._next_meter:
                    self._next_meter = now + 0.5
                    self._measure(raw)
        return got

    def _loop(self, enumerator):
        use_exclude = process_loopback_supported()
        target = None                      # (pid, exe) excluded through process loopback
        source = self._open(enumerator)
        if source:
            log("AUDIO", f"Recording desktop audio from {source.name}", "GREEN")
        next_device = next_exclude = 0.0
        while True:
            event = getattr(source, "_event", None)
            if event:   # process loopback keeps only 10 ms of sound: read it each time it signals
                kernel32.WaitForSingleObject(event, int(TICK * 1000))
            else:
                self._stop.wait(TICK)
            if self._stop.is_set():
                break
            now = time.monotonic()
            if self.exclude and now >= next_exclude:
                next_exclude = now + EXCLUDE_CHECK
                procs = processes()
                roots = excluded_roots(procs, self.exclude)
                wanted = None
                if use_exclude and len(roots) == 1:
                    wanted = (roots[0], next(e for p, _, e in procs if p == roots[0]))
                # several excluded apps at once (or Windows 10): silence the track while one of them plays
                names = {pid: exe for pid, _, exe in procs}
                self.muted = bool(roots) and not wanted and any(
                    names.get(pid) in self.exclude for pid in active_session_pids(enumerator))
                if wanted != target:
                    if source:
                        source.close()
                    target = wanted
                    source = self._open(enumerator, *(target or ()))
                    log("AUDIO", f"Desktop audio: {self.device or 'no output device'}", "GREEN")
            if not target and now >= next_device:
                next_device = now + DEVICE_CHECK
                source = self._follow_default(enumerator, source)
            try:
                got = self._drain(source, now)
            except OSError as e:
                if "0x8889" not in str(e):     # AUDCLNT_E_*: device unplugged, disabled or reconfigured
                    raise
                source.close()
                source, got, next_device = None, False, 0   # _follow_default reopens the new default
            if not got:
                self._silence(self.timeline.fill(qpc_100ns()))
                if now >= self._next_meter:
                    self.peak = 0.0
        return source

    def _follow_default(self, enumerator, source):
        """Reopen on the new default output device when the user switches (headphones, speakers, ...)."""
        device = c_void_p()
        if _com(enumerator, 4, c_int, c_int, POINTER(c_void_p))(0, 0, byref(device)) != S_OK:
            if source:
                source.close()
            self.mode, self.device = "no device", None
            return None
        dev_id = _device_id(device)
        _release(device)
        if source and source.dev_id == dev_id:
            return source
        if source:
            source.close()
        source = self._open(enumerator)
        if source:
            log("AUDIO", f"Sound output changed, now recording {source.name}", "GREEN")
        return source

# ---- the audio track of one recording ------------------------------------------------------------------------
_counter = 0
_counter_lock = threading.Lock()

class AudioTrack:
    """Desktop audio for one recording file: capture -> a small FFmpeg encoding it (Opus/AAC in Matroska) ->
    FrameKeep -> a named pipe the muxing FFmpeg reads (self.path).

    Why a separate encoder: FFmpeg lets no input run more than 0.1 s ahead of the slowest output stream, and the
    video stream trails real time by its encoder's look-ahead (many seconds at 5 FPS with repeated frames
    dropped). Raw audio sharing the video's FFmpeg was held back until its pipe overflowed, and the held-back part
    was cut off when the file stopped. Here what's held back is already compressed (a few KB per second, queued
    in this process), and on stop all of it drains into the file."""
    def __init__(self, encode_cmd, exclude=()):
        global _counter
        with _counter_lock:
            _counter += 1
            n = _counter
        self.path = rf"\\.\pipe\FrameKeep-audio-{os.getpid()}-{n}"
        self.encode_cmd, self.exclude = encode_cmd, exclude
        self.proc = self.capture = None
        self.start_time = None      # wall-clock seconds of the first sample; the video counts from here too
        self.tail = collections.deque(maxlen=5)
        self._pipe = None
        self._queue, self._cond = [], threading.Condition()
        self._eof = False
        self._connected = False
        self._threads = []

    def start(self):
        self._pipe = kernel32.CreateNamedPipeW(self.path, PIPE_ACCESS_OUTBOUND, PIPE_TYPE_BYTE | PIPE_WAIT, 1,
                                               1 << 16, 0, 0, None)
        if not self._pipe or self._pipe == INVALID_HANDLE:
            self._pipe = None
            raise OSError(f"could not create the audio pipe ({ctypes.get_last_error()})")
        t0, self.start_time = qpc_100ns(), time.time()
        try:
            self.proc = subprocess.Popen(self.encode_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        except OSError:
            kernel32.CloseHandle(self._pipe)
            raise
        self.capture = LoopbackCapture(self._feed, t0, self.exclude, on_done=self._end_input)
        self.capture.start()
        for fn in (self._read, self._send, self._errors):
            t = threading.Thread(target=fn, name="audio-relay", daemon=True)
            t.start()
            self._threads.append(t)

    def _end_input(self):
        """The capture is done: close the encoder's input so it flushes its last packets and exits right away. The
        muxer holds the video back while the audio looks behind, so the video FFmpeg can only finish once the audio
        has ended; waiting for the video before ending the audio deadlocked them."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def _feed(self, data):
        try:
            self.proc.stdin.write(data)
        except (OSError, ValueError):
            raise BrokenPipeError

    def _errors(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self.tail.append(line)

    def _read(self):
        """Encoder output -> queue. Never waits on the muxer, so the capture never stalls."""
        while chunk := self.proc.stdout.read1(1 << 16):
            with self._cond:
                self._queue.append(chunk)
                self._cond.notify()
        with self._cond:
            self._eof = True
            self._cond.notify()

    def _send(self):
        """Queue -> named pipe, at whatever pace the muxer reads."""
        try:
            if not kernel32.ConnectNamedPipe(self._pipe, None) and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
                return
            self._connected = True
            done = wintypes.DWORD()
            while True:
                with self._cond:
                    while not self._queue and not self._eof:
                        self._cond.wait()
                    if not self._queue:
                        return
                    chunk = b"".join(self._queue)
                    self._queue.clear()
                while chunk:
                    if not kernel32.WriteFile(self._pipe, chunk, len(chunk), byref(done), None):
                        return
                    chunk = chunk[done.value:]
        finally:
            kernel32.CloseHandle(self._pipe)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def finish(self):
        """End the track now (returns at once; stop() completes it)."""
        if self.capture:
            self.capture.finish()

    def stop(self, timeout=10):
        """End the track (where finish() was called, else now) and let the encoder finish. What it made waits for
        the muxer, which may open the pipe only now (it reads the video first, and a slow encoder on a still screen
        can take longer than a short file); close() once the muxer has exited."""
        if self.capture:
            self.capture.stop()
        if self.proc:
            self._end_input()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def close(self, timeout=5):
        """After the muxer exited: release the pipe (unblocking the sender if the muxer never opened it)."""
        if not self._connected and self._pipe:
            h = kernel32.CreateFileW(self.path, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
            if h and h != INVALID_HANDLE:
                kernel32.CloseHandle(h)
        for t in self._threads:
            t.join(timeout)

    def status(self):
        return self.capture.status() if self.capture else {"device": None, "mode": "starting", "peak": 0.0}
