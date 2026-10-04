"""Автозапуск программы вместе с Windows (ключ реестра HKCU\\...\\Run)."""

from __future__ import annotations

import sys
from pathlib import Path

from .config import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    if pythonw.exists():
        python = pythonw
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{python}" "{script}" --minimized'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        raise OSError("автозапуск поддерживается только в Windows")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def refresh() -> None:
    """Обновляет путь в автозапуске, если программу переместили."""
    if is_enabled():
        set_enabled(True)

