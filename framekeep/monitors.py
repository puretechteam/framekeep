# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

@dataclass
class Monitor:
    index: int                      # 1-based, primary first then left to right
    device: str                     # e.g. \\.\DISPLAY1
    rect: tuple                     # (x, y, w, h) in physical desktop pixels
    work: tuple                     # same, minus the taskbar
    primary: bool
    dxgi_output: Optional[int]      # ddagrab output_idx, None if not on the default GPU

    @property
    def label(self):
        return f"Display {self.index} ({self.rect[2]}×{self.rect[3]}" + (", primary)" if self.primary else ")")

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32), ("DesktopCoordinates", wintypes.RECT),
                ("AttachedToDesktop", wintypes.BOOL), ("Rotation", ctypes.c_uint), ("Monitor", wintypes.HMONITOR)]

IID_IDXGIFactory1 = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))

def _rect(r):
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)

def _com(obj, index, *argtypes):
    """Bind vtable slot `index` of COM object `obj` (a c_void_p) to a callable returning the raw HRESULT."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return lambda *args: fn(obj, *args)

def _release(obj):
    if obj:
        _com(obj, 2)()

def dxgi_outputs():
    """Map HMONITOR -> output index on the default adapter, which is what ffmpeg's ddagrab output_idx counts."""
    mapping = {}
    try:
        factory, adapter = ctypes.c_void_p(), ctypes.c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory)) != 0:
            return mapping
        try:
            # IDXGIFactory1::EnumAdapters1 is vtable slot 12
            if _com(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(0, ctypes.byref(adapter)) != 0:
                return mapping
            i = 0
            while True:
                output = ctypes.c_void_p()
                # IDXGIAdapter::EnumOutputs is slot 7, IDXGIOutput::GetDesc is slot 7
                if _com(adapter, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(output)) != 0:
                    break
                desc = DXGI_OUTPUT_DESC()
                if _com(output, 7, ctypes.POINTER(DXGI_OUTPUT_DESC))(ctypes.byref(desc)) == 0 and desc.Monitor:
                    mapping[int(desc.Monitor)] = i
                _release(output)
                i += 1
        finally:
            _release(adapter)
            _release(factory)
    except (OSError, AttributeError):
        pass
    return mapping

def list_monitors():
    user32 = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def callback(hmon, _hdc, _rect_ptr, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            found.append((int(hmon), info.szDevice, _rect(info.rcMonitor), _rect(info.rcWork), bool(info.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, proc(callback), 0)
    outputs = dxgi_outputs()
    found.sort(key=lambda m: (not m[4], m[2][0], m[2][1]))
    return [Monitor(i + 1, dev, rect, work, primary, outputs.get(hmon)) for i, (hmon, dev, rect, work, primary) in enumerate(found)]

def select_monitors(selection, monitors):
    """Resolve the config's monitor list ("primary", "all", or device names) against what's connected."""
    if not monitors:
        return []
    if "all" in selection:
        return list(monitors)
    chosen = [m for m in monitors if m.device in selection or ("primary" in selection and m.primary)]
    return chosen or [next((m for m in monitors if m.primary), monitors[0])]
