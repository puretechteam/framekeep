# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Small Win32 process helpers: working-set trimming, total RAM, CPU/RAM readings and tying child processes to
FrameKeep's lifetime."""

import ctypes, os, threading, time
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]

def trim_working_set():
    """Hand this process's idle pages back to Windows (after the window closes, for example)."""
    try:
        kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)
    except OSError:
        pass

_job, _job_lock = None, threading.Lock()

def own(proc):
    """Tie a child process (a subprocess.Popen) to FrameKeep: Windows ends it when FrameKeep's process ends, crash
    included, so a recorder never keeps recording on its own. Only FrameKeep's own children go in (not apps it opens
    files with). False if Windows refused."""
    global _job
    with _job_lock:
        if _job is None:
            job = kernel32.CreateJobObjectW(None, None)    # never closed: it closes when this process ends
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = job and kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                          ctypes.byref(info), ctypes.sizeof(info))
            _job = job if ok else 0
    return bool(_job) and bool(kernel32.AssignProcessToJobObject(_job, int(proc._handle)))

def physical_memory():
    """Total RAM in bytes, or 0 if unknown."""
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    return status.ullTotalPhys if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0

def _filetime(ft):
    return (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7

def process_info(pid):
    """(CPU seconds used so far, private bytes) for a process, or None if it can't be read."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        mem = PROCESS_MEMORY_COUNTERS_EX()
        mem.cb = ctypes.sizeof(mem)
        if not kernel32.GetProcessTimes(handle, *map(ctypes.byref, times)) or \
           not psapi.GetProcessMemoryInfo(handle, ctypes.byref(mem), mem.cb):
            return None
        return _filetime(times[2]) + _filetime(times[3]), mem.PrivateUsage
    finally:
        kernel32.CloseHandle(handle)

class UsageMeter:
    """CPU % (of the whole PC) and private memory for a set of processes, measured between calls."""
    def __init__(self):
        self._last = {}     # pid -> (wall time, cpu seconds)

    def sample(self, pids):
        """{pid: (cpu_percent or None on the first reading, private bytes)} for the pids that could be read."""
        now, out, seen = time.monotonic(), {}, {}
        for pid in pids:
            info = process_info(pid)
            if info is None:
                continue
            cpu, private = info
            prev = self._last.get(pid)
            pct = None
            if prev and now > prev[0]:
                pct = max(0.0, (cpu - prev[1]) / (now - prev[0]) / (os.cpu_count() or 1) * 100)
            out[pid] = (pct, private)
            seen[pid] = (now, cpu)
        self._last = seen
        return out
