# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License

import os, sys, winreg
from . import APP_NAME
from .paths import BASE_DIR

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pythonw if os.path.exists(pythonw) else sys.executable}" "{os.path.join(BASE_DIR, "FrameKeep.pyw")}" --minimized'

def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False

def set_enabled(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
