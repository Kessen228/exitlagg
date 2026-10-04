"""Защита от запуска второй копии программы.

Вторая копия не запускается, а оставляет файл-запрос, по которому уже работающая
копия показывает своё окно.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import APP_NAME


class SingleInstance:
    def __init__(self, directory: Path):
        self._request = directory / "show.request"
        self._lock_path = directory / "instance.lock"
        self._handle = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}_SingleInstance")
            if handle and ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True

        import fcntl

        handle = open(self._lock_path, "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def request_show(self) -> None:
        try:
            self._request.touch()
        except OSError:
            pass

    def show_requested(self) -> bool:
        if not self._request.exists():
            return False
        try:
            self._request.unlink()
        except OSError:
            pass
        return True
