import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from launcher_scheduler.config import Config, Launcher, Session, State, Store, Task


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = Store(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run(self):
        config, first_run = self.store.load_config()
        self.assertTrue(first_run)
        self.assertEqual(config.launchers, [])

    def test_config_round_trip(self):
        launcher = Launcher(name="Steam", path=r"C:\Steam\steam.exe", args="-silent",
                            process_names=["steam.exe"], close_args="-shutdown", preset="steam")
        task = Task(launcher_ids=[launcher.id], time="03:30", days=[0, 2], duration_min=90)
        config = Config(launchers=[launcher], tasks=[task])
        config.settings.idle_minutes = 7
        self.store.save_config(config)
        loaded, first_run = self.store.load_config()
        self.assertFalse(first_run)
        self.assertEqual(loaded, config)

    def test_unknown_fields_and_broken_items_are_ignored(self):
        data = {
            "launchers": [{"name": "A", "path": "a.exe", "future_field": 1}, {"path": "no-name.exe"}],
            "tasks": [{"launcher_ids": ["x"], "time": "01:00"}],
            "settings": {"idle_minutes": 3, "removed_option": True},
        }
        self.store.config_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertLogs("launcher_scheduler", "WARNING"):
            config, _ = self.store.load_config()
        self.assertEqual([l.name for l in config.launchers], ["A"])
        self.assertEqual(config.tasks[0].time, "01:00")
        self.assertEqual(config.settings.idle_minutes, 3)

    def test_corrupted_config_is_backed_up(self):
        self.store.config_path.write_text("{not json", encoding="utf-8")
        with self.assertLogs("launcher_scheduler", "ERROR"):
            config, first_run = self.store.load_config()
        self.assertFalse(first_run)
        self.assertEqual(config, Config())
        self.assertTrue((self.dir / "config.json.bad").exists())

    def test_state_round_trip(self):
        state = State(
            sessions={"s": Session("s", end=datetime(2026, 10, 5, 6, 0), started=datetime(2026, 10, 5, 4, 0),
                                   task_id="t", postponed=True)},
            handled={"t": datetime(2026, 10, 5, 4, 0)},
        )
        self.store.save_state(state)
        self.assertEqual(self.store.load_state(), state)

    def test_default_process_names(self):
        self.assertEqual(Launcher(name="A", path=r"C:\Games\Launcher.EXE").process_specs(), ["Launcher.EXE"])
        self.assertEqual(Launcher(name="A", path="shortcut.lnk").process_specs(), [])
        self.assertEqual(Launcher(name="A", path="a.exe", process_names=["b.exe"]).process_specs(), ["b.exe"])


if __name__ == "__main__":
    unittest.main()
