"""Запуск, поиск и закрытие процессов лаунчеров."""

from __future__ import annotations

import ctypes
import os
import re
import shlex
import subprocess
import sys

import psutil

from .config import Launcher

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

_ENV_VAR = re.compile(r"%([^%]+)%")


def expand(path: str) -> str:
    """Подставляет переменные окружения вида %ProgramFiles% и ~."""
    expanded = _ENV_VAR.sub(lambda m: os.environ.get(m.group(1), m.group(0)), path.strip().strip('"'))
    return os.path.expanduser(expanded)


def _normpath(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _split_specs(launcher: Launcher) -> tuple[set[str], set[str]]:
    """Разделяет описание процессов на имена exe и полные пути."""
    names, paths = set(), set()
    for spec in launcher.process_specs():
        spec = expand(spec)
        if not spec:
            continue
        if "\\" in spec or "/" in spec:
            paths.add(_normpath(spec))
        else:
            names.add(spec.lower())
    return names, paths


class ProcessManager:
    def _scan(self) -> list[psutil.Process]:
        if IS_WINDOWS:
            return list(psutil.process_iter(["name", "exe"]))
        # Завершившиеся, но ещё не убранные системой процессы (зомби) не считаем запущенными.
        return [p for p in psutil.process_iter(["name", "exe", "status"])
                if p.info.get("status") != psutil.STATUS_ZOMBIE]

    @staticmethod
    def _matches(proc: psutil.Process, names: set[str], paths: set[str]) -> bool:
        name = (proc.info.get("name") or "").lower()
        if name in names:
            return True
        exe = proc.info.get("exe")
        return bool(paths and exe and _normpath(exe) in paths)

    def find(self, launcher: Launcher, procs=None) -> list[psutil.Process]:
        names, paths = _split_specs(launcher)
        if not names and not paths:
            return []
        procs = self._scan() if procs is None else procs
        return [p for p in procs if self._matches(p, names, paths)]

    def is_running(self, launcher: Launcher) -> bool:
        return bool(self.find(launcher))

    def running_map(self, launchers: list[Launcher]) -> dict[str, bool]:
        procs = self._scan()
        return {launcher.id: bool(self.find(launcher, procs)) for launcher in launchers}

    def _execute(self, path: str, args: str) -> None:
        path = expand(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"файл не найден: {path}")
        workdir = os.path.dirname(path) or None
        if IS_WINDOWS:
            # ShellExecute: работает с .exe, ярлыками .lnk и при необходимости покажет запрос UAC.
            os.startfile(path, "open", args or "", workdir)
        else:
            subprocess.Popen(
                [path, *shlex.split(args or "")],
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    def start(self, launcher: Launcher) -> None:
        self._execute(launcher.path, launcher.args)

    def request_close(self, launcher: Launcher) -> None:
        """Мягкое закрытие: команда закрытия лаунчера или сигнал окну/процессу."""
        if launcher.close_args:
            self._execute(launcher.path, launcher.close_args)
            return
        procs = self.find(launcher)
        if not procs:
            return
        if IS_WINDOWS:
            # Без /F taskkill отправляет окну WM_CLOSE. Дерево процессов (/T) не трогаем,
            # чтобы не закрыть запущенную из лаунчера игру.
            cmd = ["taskkill"]
            for proc in procs:
                cmd += ["/PID", str(proc.pid)]
            # stdin тоже перенаправляем: в exe без консоли стандартные дескрипторы недействительны.
            subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=CREATE_NO_WINDOW, check=False)
        else:
            for proc in procs:
                try:
                    proc.terminate()
                except psutil.Error:
                    pass

    def kill(self, launcher: Launcher) -> tuple[int, int]:
        """Принудительно завершает процессы лаунчера. Возвращает (завершено, отказано в доступе)."""
        killed = denied = 0
        for proc in self.find(launcher):
            try:
                proc.kill()
                killed += 1
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied += 1
        return killed, denied

    def boot_time(self) -> float:
        return psutil.boot_time()


def idle_seconds() -> float | None:
    """Сколько секунд не было ввода с клавиатуры и мыши (None, если узнать нельзя)."""
    if not IS_WINDOWS:
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    get_tick_count = ctypes.windll.kernel32.GetTickCount
    get_tick_count.restype = ctypes.c_uint32
    return ((get_tick_count() - info.dwTime) & 0xFFFFFFFF) / 1000.0
