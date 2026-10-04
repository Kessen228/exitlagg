import unittest
from datetime import datetime, timedelta

from launcher_scheduler.config import Config, Launcher, Session, Settings, State, Task
from launcher_scheduler.scheduler import Scheduler

MON = datetime(2026, 10, 5)  # понедельник


def at(day: datetime, clock: str, seconds: int = 0) -> datetime:
    hours, minutes = map(int, clock.split(":"))
    return day.replace(hour=hours, minute=minutes, second=seconds)


class FakeProcs:
    def __init__(self):
        self.running: set[str] = set()
        self.started: list[str] = []
        self.close_requests: list[str] = []
        self.killed: list[str] = []
        self.close_works = True
        self.boot = 0.0

    def is_running(self, launcher):
        return launcher.id in self.running

    def running_map(self, launchers):
        return {l.id: l.id in self.running for l in launchers}

    def start(self, launcher):
        if launcher.path == "missing.exe":
            raise FileNotFoundError("файл не найден")
        self.started.append(launcher.id)
        self.running.add(launcher.id)

    def request_close(self, launcher):
        self.close_requests.append(launcher.id)
        if self.close_works:
            self.running.discard(launcher.id)

    def kill(self, launcher):
        self.killed.append(launcher.id)
        self.running.discard(launcher.id)
        return 1, 0

    def boot_time(self):
        return self.boot


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.steam = Launcher(name="Steam", path="steam.exe", id="steam")
        self.epic = Launcher(name="Epic", path="epic.exe", id="epic")
        self.settings = Settings(launch_interval_sec=0, close_timeout_sec=0)
        self.config = Config(launchers=[self.steam, self.epic], settings=self.settings)
        self.procs = FakeProcs()
        self.idle = None
        self.state = State()
        self.scheduler = Scheduler(self.config, self.state, self.procs, idle=lambda: self.idle)

    def add_task(self, clock="04:00", duration=60, launchers=("steam",), days=range(7), seen=True, **kwargs):
        task = Task(launcher_ids=list(launchers), time=clock, days=list(days), duration_min=duration,
                    id=f"t{len(self.config.tasks)}", **kwargs)
        self.config.tasks.append(task)
        if seen:
            # задача существовала раньше: прошлый запуск был вчера
            self.state.handled[task.id] = at(MON - timedelta(days=1), clock)
        return task

    def test_starts_on_time_and_closes_after_duration(self):
        self.add_task("04:00", 60, launchers=("steam", "epic"))
        self.scheduler.tick(at(MON, "03:59"))
        self.assertEqual(self.procs.started, [])

        self.scheduler.tick(at(MON, "04:00", 5))
        self.assertEqual(self.procs.started, ["steam", "epic"])
        self.assertEqual(self.state.sessions["steam"].end, at(MON, "05:00"))

        self.scheduler.tick(at(MON, "04:30"))
        self.assertEqual(self.procs.started, ["steam", "epic"], "повторного запуска быть не должно")
        self.assertEqual(self.procs.close_requests, [])

        self.scheduler.tick(at(MON, "05:00", 3))
        self.assertEqual(self.procs.close_requests, ["steam", "epic"])
        self.assertEqual(self.procs.killed, [])
        self.assertEqual(self.state.sessions, {})

    def test_runs_again_next_day(self):
        self.add_task("04:00", 30)
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.tick(at(MON, "04:31"))
        self.scheduler.tick(at(MON + timedelta(days=1), "04:00", 2))
        self.assertEqual(self.procs.started, ["steam", "steam"])

    def test_respects_days_of_week(self):
        self.add_task("04:00", 30, days=[2])  # только среда
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.tick(at(MON + timedelta(days=1), "04:00"))
        self.assertEqual(self.procs.started, [])
        self.scheduler.tick(at(MON + timedelta(days=2), "04:00", 1))
        self.assertEqual(self.procs.started, ["steam"])

    def test_disabled_task_is_ignored(self):
        self.add_task("04:00", 30, enabled=False)
        self.scheduler.tick(at(MON, "04:00"))
        self.assertEqual(self.procs.started, [])

    def test_preexisting_launcher_is_left_open(self):
        self.procs.running.add("steam")
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.assertEqual(self.procs.started, [])
        self.assertNotIn("steam", self.state.sessions)
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, [])
        self.assertIn("steam", self.procs.running)

    def test_preexisting_launcher_closed_when_setting_disabled(self):
        self.settings.keep_preexisting = False
        self.procs.running.add("steam")
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, ["steam"])

    def test_unlimited_task_does_not_close(self):
        self.add_task("04:00", 0)
        self.scheduler.tick(at(MON, "04:00"))
        self.assertEqual(self.procs.started, ["steam"])
        self.assertEqual(self.state.sessions, {})

    def test_catch_up_inside_working_window(self):
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:40"))
        self.assertEqual(self.procs.started, ["steam"])
        self.assertEqual(self.state.sessions["steam"].end, at(MON, "05:00"))

    def test_no_catch_up_when_disabled(self):
        self.settings.catch_up_missed = False
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:40"))
        self.assertEqual(self.procs.started, [])
        self.assertEqual(self.state.handled["t0"], at(MON, "04:00"))

    def test_missed_after_window_is_skipped(self):
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "05:10"))
        self.assertEqual(self.procs.started, [])
        self.assertEqual(self.state.handled["t0"], at(MON, "04:00"))

    def test_new_task_does_not_fire_retroactively(self):
        self.add_task("04:00", 120, seen=False)
        self.scheduler.tick(at(MON, "04:30"))
        self.assertEqual(self.procs.started, [])
        self.scheduler.tick(at(MON + timedelta(days=1), "04:00", 30))
        self.assertEqual(self.procs.started, ["steam"])

    def test_new_task_created_just_before_start_fires(self):
        self.add_task("04:00", 60, seen=False)
        self.scheduler.tick(at(MON, "03:58"))
        self.scheduler.tick(at(MON, "04:00", 10))
        self.assertEqual(self.procs.started, ["steam"])

    def test_overlapping_tasks_extend_session(self):
        self.add_task("04:00", 60)
        self.add_task("04:30", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.tick(at(MON, "04:30"))
        self.assertEqual(self.procs.started, ["steam"])
        self.assertEqual(self.state.sessions["steam"].end, at(MON, "05:30"))
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, [])
        self.scheduler.tick(at(MON, "05:30"))
        self.assertEqual(self.procs.close_requests, ["steam"])

    def test_postpones_close_while_user_is_active(self):
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.idle = 30  # кто-то за компьютером
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, [])
        self.assertEqual(self.state.sessions["steam"].end, at(MON, "05:05"))
        self.idle = 600
        self.scheduler.tick(at(MON, "05:05"))
        self.assertEqual(self.procs.close_requests, ["steam"])

    def test_closes_regardless_of_activity_when_disabled(self):
        self.settings.postpone_if_active = False
        self.idle = 1
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, ["steam"])

    def test_force_kill_when_graceful_close_fails(self):
        self.procs.close_works = False
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, ["steam"])
        self.assertEqual(self.procs.killed, ["steam"])
        self.assertNotIn("steam", self.procs.running)

    def test_launcher_closed_by_user_is_not_touched(self):
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.procs.running.discard("steam")
        self.scheduler.tick(at(MON, "05:00"))
        self.assertEqual(self.procs.close_requests, [])
        self.assertEqual(self.state.sessions, {})

    def test_missing_launcher_file(self):
        self.steam.path = "missing.exe"
        self.add_task("04:00", 60)
        with self.assertLogs("launcher_scheduler", "ERROR"):
            self.scheduler.tick(at(MON, "04:00"))
        self.assertEqual(self.state.sessions, {})

    def test_update_config_resets_changed_task(self):
        task = self.add_task("04:00", 120)
        self.scheduler.update_config(self.config, changed_tasks=[task.id])
        self.scheduler._drain_jobs()
        self.assertNotIn(task.id, self.state.handled)
        # время изменённой задачи уже прошло — догоняющего запуска нет
        self.scheduler.tick(at(MON, "04:30"))
        self.assertEqual(self.procs.started, [])

    def test_sessions_from_before_reboot_are_dropped(self):
        self.state.sessions["steam"] = Session("steam", end=at(MON, "05:00"), started=at(MON, "04:00"))
        self.procs.boot = at(MON, "04:30").timestamp()
        self.scheduler._prune_stale_sessions()
        self.assertEqual(self.state.sessions, {})

    def test_sessions_after_boot_are_kept(self):
        self.state.sessions["steam"] = Session("steam", end=at(MON, "05:00"), started=at(MON, "04:00"))
        self.procs.boot = at(MON, "03:00").timestamp()
        self.scheduler._prune_stale_sessions()
        self.assertIn("steam", self.state.sessions)

    def test_run_task_now(self):
        task = self.add_task("04:00", 45)
        self.scheduler.run_task_now(task.id)
        before = datetime.now()
        self.scheduler._drain_jobs()
        self.assertEqual(self.procs.started, ["steam"])
        end = self.state.sessions["steam"].end
        self.assertAlmostEqual((end - before).total_seconds(), 45 * 60, delta=5)

    def test_close_now_drops_session(self):
        self.add_task("04:00", 60)
        self.scheduler.tick(at(MON, "04:00"))
        self.scheduler.close_now("steam")
        self.scheduler._drain_jobs()
        self.assertEqual(self.procs.close_requests, ["steam"])
        self.assertEqual(self.state.sessions, {})


if __name__ == "__main__":
    unittest.main()
