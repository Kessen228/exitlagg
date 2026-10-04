import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from launcher_scheduler import presets
from launcher_scheduler.config import Launcher
from launcher_scheduler.processes import ProcessManager, expand


class FakeProc:
    def __init__(self, name, exe=None):
        self.info = {"name": name, "exe": exe}


class MatchingTest(unittest.TestCase):
    def test_expand(self):
        os.environ["LS_TEST_DIR"] = "/opt/games"
        self.assertEqual(expand('"%LS_TEST_DIR%/steam.exe"'), "/opt/games/steam.exe")
        self.assertEqual(expand("%LS_UNKNOWN_VAR%/a.exe"), "%LS_UNKNOWN_VAR%/a.exe")

    def test_find_by_name_and_path(self):
        launcher = Launcher(name="R", path="/games/rockstar/LauncherPatcher.exe",
                            process_names=["/games/rockstar/Launcher.exe", "SocialClubHelper.exe"])
        procs = [
            FakeProc("Launcher.exe", "/games/rockstar/Launcher.exe"),
            FakeProc("Launcher.exe", "/other/app/Launcher.exe"),  # чужой процесс с тем же именем
            FakeProc("socialclubhelper.exe", None),
            FakeProc("explorer.exe", "/windows/explorer.exe"),
        ]
        found = ProcessManager().find(launcher, procs)
        self.assertEqual([p.info["exe"] for p in found], ["/games/rockstar/Launcher.exe", None])

    def test_make_launcher_substitutes_install_dir(self):
        preset = presets.by_key("rockstar")
        launcher = presets.make_launcher(preset, os.path.join("games", "Launcher", "LauncherPatcher.exe"))
        self.assertEqual(launcher.process_names[0], os.path.join("games", "Launcher") + "\\Launcher.exe")
        self.assertEqual(launcher.preset, "rockstar")


class PresetSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_exe(self, *parts) -> str:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_found_via_uninstall_install_location(self):
        exe = self.make_exe("Epic Games", "Launcher", "Portal", "Binaries", "Win64", "EpicGamesLauncher.exe")
        entries = [{"DisplayName": "Epic Games Launcher", "InstallLocation": str(self.root / "Epic Games")}]
        self.assertEqual(presets.find_path(presets.by_key("epic"), entries, []), os.path.normpath(exe))

    def test_found_via_uninstall_display_icon(self):
        exe = self.make_exe("Steam", "steam.exe")
        entries = [{"DisplayName": "Steam", "DisplayIcon": f'"{exe}",0'}]
        self.assertEqual(presets.find_path(presets.by_key("steam"), entries, []), exe)

    def test_found_via_known_folder(self):
        os.environ["LS_TEST_PROGRAMDATA"] = str(self.root)
        exe = self.make_exe("Lesta", "GameCenter", "lgc.exe")
        preset = presets.Preset(key="x", name="X", exe="lgc.exe", dirs=("%LS_TEST_PROGRAMDATA%/Lesta/GameCenter",))
        self.assertEqual(presets.find_path(preset, [], []), os.path.normpath(exe))

    def test_not_found(self):
        self.assertIsNone(presets.find_path(presets.by_key("gog"), [], []))

    def test_icon_path(self):
        self.assertEqual(presets._icon_path(r'"C:\A B\x.exe",0'), r"C:\A B\x.exe")
        self.assertEqual(presets._icon_path(r"C:\A\x.exe,-101"), r"C:\A\x.exe")
        self.assertEqual(presets._icon_path(r"C:\A\x.exe"), r"C:\A\x.exe")


@unittest.skipIf(sys.platform == "win32", "проверка на настоящих процессах написана для POSIX")
class RealProcessTest(unittest.TestCase):
    def test_start_detect_and_close(self):
        sleep = shutil.which("sleep")
        if not sleep:
            self.skipTest("нет команды sleep")
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "ls_test_sleeper")
            shutil.copy(sleep, exe)
            launcher = Launcher(name="Test", path=exe, args="60", process_names=["ls_test_sleeper"])
            manager = ProcessManager()
            self.assertFalse(manager.is_running(launcher))
            manager.start(launcher)
            self.assertTrue(self._wait(lambda: manager.is_running(launcher)))
            self.assertTrue(manager.running_map([launcher])[launcher.id])
            manager.request_close(launcher)
            self.assertTrue(self._wait(lambda: not manager.is_running(launcher)))

    def test_start_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            ProcessManager().start(Launcher(name="X", path="/nonexistent/launcher.exe"))

    @staticmethod
    def _wait(condition, timeout=5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.1)
        return False


if __name__ == "__main__":
    unittest.main()
