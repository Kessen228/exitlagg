"""Известные лаунчеры и автоматический поиск их на компьютере (Windows)."""

from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass

from .config import Launcher
from .processes import expand

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    exe: str  # путь к exe относительно папки установки
    dirs: tuple[str, ...] = ()  # типичные папки установки; "*:" — любой локальный диск
    uninstall: tuple[str, ...] = ()  # DisplayName в списке установленных программ
    registry: tuple[tuple[str, str, str], ...] = ()  # (HKCU/HKLM, ключ, значение) с путём к exe или папке
    # Процессы лаунчера; {dir} заменяется на папку установки (для неоднозначных имён вроде Launcher.exe).
    processes: tuple[str, ...] = ()
    args: str = ""
    close_args: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="steam",
        name="Steam",
        exe="steam.exe",
        dirs=(r"%ProgramFiles(x86)%\Steam", r"%ProgramFiles%\Steam", r"*:\Steam", r"*:\Games\Steam",
              r"*:\Program Files (x86)\Steam", r"*:\Program Files\Steam"),
        uninstall=("Steam",),
        registry=(("HKCU", r"Software\Valve\Steam", "SteamExe"),
                  ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                  ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath")),
        processes=("steam.exe", "steamwebhelper.exe"),
        args="-silent",
        close_args="-shutdown",
    ),
    Preset(
        key="epic",
        name="Epic Games Launcher",
        exe=r"Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
        dirs=(r"%ProgramFiles(x86)%\Epic Games", r"%ProgramFiles%\Epic Games", r"*:\Epic Games",
              r"*:\Program Files (x86)\Epic Games", r"*:\Program Files\Epic Games"),
        uninstall=("Epic Games Launcher",),
        processes=("EpicGamesLauncher.exe", "EpicWebHelper.exe"),
        args="-silent",
    ),
    Preset(
        key="ea",
        name="EA app",
        exe=r"EA Desktop\EALauncher.exe",
        dirs=(r"%ProgramFiles%\Electronic Arts\EA Desktop", r"*:\Program Files\Electronic Arts\EA Desktop"),
        uninstall=("EA app",),
        processes=("EADesktop.exe", "EALauncher.exe"),
    ),
    Preset(
        key="ubisoft",
        name="Ubisoft Connect",
        exe="UbisoftConnect.exe",
        dirs=(r"%ProgramFiles(x86)%\Ubisoft\Ubisoft Game Launcher", r"%ProgramFiles%\Ubisoft\Ubisoft Game Launcher",
              r"*:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher", r"*:\Ubisoft\Ubisoft Game Launcher"),
        uninstall=("Ubisoft Connect", "Uplay"),
        processes=("UbisoftConnect.exe", "upc.exe", "UplayWebCore.exe"),
    ),
    Preset(
        key="battlenet",
        name="Battle.net",
        exe="Battle.net Launcher.exe",
        dirs=(r"%ProgramFiles(x86)%\Battle.net", r"%ProgramFiles%\Battle.net", r"*:\Battle.net",
              r"*:\Program Files (x86)\Battle.net"),
        uninstall=("Battle.net",),
        processes=("Battle.net.exe", "Battle.net Launcher.exe"),
    ),
    Preset(
        key="gog",
        name="GOG GALAXY",
        exe="GalaxyClient.exe",
        dirs=(r"%ProgramFiles(x86)%\GOG Galaxy", r"%ProgramFiles%\GOG Galaxy", r"*:\GOG Galaxy",
              r"*:\Program Files (x86)\GOG Galaxy"),
        uninstall=("GOG GALAXY",),
        processes=("GalaxyClient.exe", "GalaxyClient Helper.exe", "GOG Galaxy Notifications Renderer.exe"),
    ),
    Preset(
        key="rockstar",
        name="Rockstar Games Launcher",
        exe="LauncherPatcher.exe",
        dirs=(r"%ProgramFiles%\Rockstar Games\Launcher", r"*:\Program Files\Rockstar Games\Launcher",
              r"*:\Rockstar Games\Launcher"),
        uninstall=("Rockstar Games Launcher",),
        processes=(r"{dir}\Launcher.exe", "LauncherPatcher.exe", "SocialClubHelper.exe"),
    ),
    Preset(
        key="riot",
        name="Riot Client",
        exe="RiotClientServices.exe",
        dirs=(r"*:\Riot Games\Riot Client", r"*:\Games\Riot Games\Riot Client"),
        processes=("RiotClientServices.exe", "Riot Client.exe", "RiotClientUx.exe", "RiotClientUxRender.exe"),
    ),
    Preset(
        key="lesta",
        name="Lesta Игровой центр",
        exe="lgc.exe",
        dirs=(r"%ProgramData%\Lesta\GameCenter",),
        processes=("lgc.exe",),
    ),
    Preset(
        key="wgc",
        name="Wargaming.net Game Center",
        exe="wgc.exe",
        dirs=(r"%ProgramData%\Wargaming.net\GameCenter",),
        processes=("wgc.exe",),
    ),
    Preset(
        key="vkplay",
        name="VK Play",
        exe="GameCenter.exe",
        dirs=(r"%LOCALAPPDATA%\GameCenter",),
        processes=(r"{dir}\GameCenter.exe",),
    ),
    Preset(
        key="amazon",
        name="Amazon Games",
        exe=r"App\Amazon Games.exe",
        dirs=(r"%LOCALAPPDATA%\Amazon Games",),
        processes=("Amazon Games.exe", "Amazon Games UI.exe"),
    ),
)


def by_key(key: str) -> Preset | None:
    return next((p for p in PRESETS if p.key == key), None)


def _join(directory: str, relative: str) -> str:
    return os.path.normpath(os.path.join(directory, *relative.replace("\\", "/").split("/")))


def make_launcher(preset: Preset, path: str) -> Launcher:
    directory = os.path.dirname(path)
    processes = [p.replace("{dir}", directory) for p in preset.processes]
    return Launcher(
        name=preset.name,
        path=path,
        args=preset.args,
        process_names=processes,
        close_args=preset.close_args,
        preset=preset.key,
    )


# ---- поиск на диске и в реестре ----

def _fixed_drives() -> list[str]:
    if not IS_WINDOWS:
        return []
    import ctypes

    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if mask & (1 << index) and kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
            drives.append(f"{letter}:")
    return drives


def _registry_value(hive: str, key: str, name: str) -> str | None:
    if not IS_WINDOWS:
        return None
    import winreg

    root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(root, key) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    return str(value) if value else None


def _uninstall_entries() -> list[dict[str, str]]:
    if not IS_WINDOWS:
        return []
    import winreg

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    entries = []
    for root, path in roots:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, sub) as handle:
                        entry = {}
                        for name in ("DisplayName", "InstallLocation", "DisplayIcon"):
                            try:
                                entry[name] = str(winreg.QueryValueEx(handle, name)[0])
                            except OSError:
                                pass
                        entries.append(entry)
                except OSError:
                    continue
    return entries


def _icon_path(icon: str) -> str:
    """DisplayIcon имеет вид "C:\\path\\app.exe",0 — убираем кавычки и индекс иконки."""
    icon = icon.strip()
    if icon.startswith('"'):
        return icon[1:].split('"', 1)[0]
    return icon.rsplit(",", 1)[0] if icon.rsplit(",", 1)[-1].strip().lstrip("-").isdigit() else icon


def _candidates(preset: Preset, uninstall: list[dict[str, str]], drives: list[str]):
    exe_name = os.path.basename(preset.exe.replace("\\", "/")).lower()
    for hive, key, name in preset.registry:
        value = _registry_value(hive, key, name)
        if value:
            yield os.path.normpath(value) if value.lower().endswith(".exe") else _join(value, preset.exe)
    wanted = {name.lower() for name in preset.uninstall}
    for entry in uninstall:
        if entry.get("DisplayName", "").strip().lower() not in wanted:
            continue
        if entry.get("InstallLocation"):
            yield _join(entry["InstallLocation"].strip().strip('"'), preset.exe)
        if entry.get("DisplayIcon"):
            icon = _icon_path(entry["DisplayIcon"])
            if os.path.basename(icon).lower() == exe_name:
                yield icon
    for directory in preset.dirs:
        if directory.startswith("*:"):
            for drive in drives:
                yield _join(drive + directory[2:], preset.exe)
        else:
            expanded = expand(directory)
            if "%" not in expanded:
                yield _join(expanded, preset.exe)


def find_path(preset: Preset, uninstall=None, drives=None) -> str | None:
    uninstall = _uninstall_entries() if uninstall is None else uninstall
    drives = _fixed_drives() if drives is None else drives
    for candidate in _candidates(preset, uninstall, drives):
        if os.path.isfile(candidate):
            return candidate
    return None


def detect_installed(existing: list[Launcher]) -> list[Launcher]:
    """Ищет установленные лаунчеры, которых ещё нет в списке."""
    known_presets = {launcher.preset for launcher in existing if launcher.preset}
    known_paths = {os.path.normcase(os.path.normpath(expand(l.path))) for l in existing if l.path}
    uninstall = _uninstall_entries()
    drives = _fixed_drives()
    found = []
    for preset in PRESETS:
        if preset.key in known_presets:
            continue
        path = find_path(preset, uninstall, drives)
        if path and os.path.normcase(path) not in known_paths:
            found.append(make_launcher(preset, path))
    return found
