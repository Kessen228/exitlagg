"""Фоновый планировщик: запускает лаунчеры по расписанию и закрывает их по истечении времени.

Все изменения состояния происходят в одном фоновом потоке; окно программы общается
с ним через очередь команд (`submit`) и читает снимок состояния (`snapshot`).
"""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import time
from datetime import datetime, timedelta

from .config import Config, Launcher, Session, State, Task
from .processes import idle_seconds
from .schedule import latest_occurrence

log = logging.getLogger("launcher_scheduler")

TICK_SECONDS = 10
# Насколько можно опоздать с запуском, чтобы он не считался пропущенным.
GRACE = timedelta(minutes=2)
# Окно догоняющего запуска для задач без ограничения времени работы.
UNLIMITED_CATCH_UP = timedelta(hours=1)
POSTPONE_STEP = timedelta(minutes=5)


class Scheduler:
    def __init__(self, config: Config, state: State, procs, save_state=None, idle=idle_seconds):
        self.config = config
        self.state = state
        self.procs = procs
        self._save_state = save_state or (lambda state: None)
        self._idle = idle
        self._jobs: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running: dict[str, bool] = {}
        self._sessions_view: dict[str, Session] = {}
        self._thread: threading.Thread | None = None

    # ---- вызывается из любого потока ----

    def start(self) -> None:
        self._prune_stale_sessions()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def submit(self, job) -> None:
        self._jobs.put(job)
        self._wake.set()

    def update_config(self, config: Config, changed_tasks=()) -> None:
        """Подменяет настройки. Для изменённых задач сбрасывается отметка о запуске,
        чтобы задача не стартовала сразу, если её время уже прошло."""

        def apply():
            for task_id in list(self.state.handled):
                if task_id in changed_tasks or config.task(task_id) is None:
                    del self.state.handled[task_id]
            self.config = config
            self._save_state(self.state)

        self.submit(apply)

    def run_task_now(self, task_id: str) -> None:
        def job():
            task = self.config.task(task_id)
            if task is None:
                return
            end = None
            if task.duration_min > 0:
                end = datetime.now() + timedelta(minutes=task.duration_min)
            self._run_task(task, end, manual=True)

        self.submit(job)

    def launch_now(self, launcher_id: str) -> None:
        def job():
            launcher = self.config.launcher(launcher_id)
            if launcher is None:
                return
            if self.procs.is_running(launcher):
                log.info("%s уже запущен", launcher.name)
            else:
                self._launch(launcher)

        self.submit(job)

    def close_now(self, launcher_id: str) -> None:
        def job():
            launcher = self.config.launcher(launcher_id)
            if launcher is None:
                return
            if self.state.sessions.pop(launcher_id, None):
                self._save_state(self.state)
            self._close(launcher)

        self.submit(job)

    def snapshot(self) -> tuple[dict[str, bool], dict[str, Session]]:
        with self._lock:
            return dict(self._running), dict(self._sessions_view)

    # ---- фоновый поток ----

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._drain_jobs()
            try:
                self.tick()
            except Exception:
                log.exception("Ошибка планировщика")
            self._refresh_status()
            self._wake.wait(TICK_SECONDS)
            self._wake.clear()

    def _drain_jobs(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            try:
                job()
            except Exception:
                log.exception("Ошибка при выполнении команды")
            self._refresh_status()

    def _refresh_status(self) -> None:
        try:
            running = self.procs.running_map(self.config.launchers)
        except Exception:
            log.exception("Не удалось получить список процессов")
            running = {}
        with self._lock:
            self._running = running
            self._sessions_view = {
                key: dataclasses.replace(value) for key, value in self.state.sessions.items()
            }

    def _prune_stale_sessions(self) -> None:
        """Сеансы, начатые до перезагрузки компьютера, больше не относятся к запущенным процессам."""
        try:
            boot = datetime.fromtimestamp(self.procs.boot_time())
        except Exception:
            return
        stale = [key for key, session in self.state.sessions.items() if session.started < boot]
        for key in stale:
            del self.state.sessions[key]
        if stale:
            self._save_state(self.state)

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        for task in list(self.config.tasks):
            if self._stop.is_set():
                return
            self._check_task(task, now)
        self._check_sessions(now)

    def _catch_up_window(self, task: Task) -> timedelta:
        if not self.config.settings.catch_up_missed:
            return GRACE
        if task.duration_min > 0:
            return timedelta(minutes=task.duration_min)
        return UNLIMITED_CATCH_UP

    def _check_task(self, task: Task, now: datetime) -> None:
        if not task.enabled or not task.days or not task.launcher_ids:
            return
        try:
            occurrence = latest_occurrence(task.time, task.days, now)
        except ValueError:
            return
        if occurrence is None:
            return
        handled = self.state.handled.get(task.id)
        if handled is not None and handled >= occurrence:
            return
        self.state.handled[task.id] = occurrence
        self._save_state(self.state)

        names = self._launcher_names(task)
        late = now - occurrence
        end = occurrence + timedelta(minutes=task.duration_min) if task.duration_min > 0 else None
        # Для новой или только что изменённой задачи догоняющий запуск не делаем.
        window = self._catch_up_window(task) if handled is not None else GRACE
        if late > window or (end is not None and end - now < timedelta(minutes=1)):
            if handled is not None:
                log.warning(
                    "Пропущен запуск %s в %s: программа не работала или компьютер был выключен",
                    names, occurrence.strftime("%d.%m %H:%M"),
                )
            return
        if late > GRACE:
            log.info("Догоняющий запуск (по расписанию был в %s)", occurrence.strftime("%H:%M"))
        self._run_task(task, end)

    def _launcher_names(self, task: Task) -> str:
        names = [l.name for i in task.launcher_ids if (l := self.config.launcher(i))]
        return ", ".join(names) or "—"

    def _run_task(self, task: Task, end: datetime | None, manual: bool = False) -> None:
        launchers = [l for i in task.launcher_ids if (l := self.config.launcher(i))]
        until = f"до {end.strftime('%H:%M')}" if end else "без автоматического закрытия"
        kind = "Ручной запуск задачи" if manual else "Запуск по расписанию"
        log.info("%s: %s (%s)", kind, self._launcher_names(task), until)
        interval = max(0, self.config.settings.launch_interval_sec)
        for index, launcher in enumerate(launchers):
            if index and interval and self._stop.wait(interval):
                return
            self._start_launcher(launcher, end, task.id)
            self._refresh_status()

    def _start_launcher(self, launcher: Launcher, end: datetime | None, task_id: str) -> None:
        running = self.procs.is_running(launcher)
        session = self.state.sessions.get(launcher.id)
        if session is not None:
            # Лаунчер уже работает по другой задаче — продлеваем время работы.
            if end is None:
                del self.state.sessions[launcher.id]
                log.info("%s: автоматическое закрытие отменено", launcher.name)
            elif end > session.end:
                session.end = end
                log.info("%s: время работы продлено до %s", launcher.name, end.strftime("%H:%M"))
            self._save_state(self.state)
            if not running:
                self._launch(launcher)
            return

        if running:
            if end is not None and self.config.settings.keep_preexisting:
                log.info("%s уже был открыт — закрываться по расписанию не будет", launcher.name)
                return
            log.info("%s уже запущен", launcher.name)
        elif not self._launch(launcher):
            return

        if end is not None:
            self.state.sessions[launcher.id] = Session(
                launcher_id=launcher.id, end=end, started=datetime.now(), task_id=task_id
            )
            self._save_state(self.state)

    def _launch(self, launcher: Launcher) -> bool:
        try:
            self.procs.start(launcher)
        except OSError as exc:
            log.error("Не удалось запустить %s: %s", launcher.name, exc)
            return False
        log.info("Запущен %s", launcher.name)
        return True

    def _user_is_active(self) -> bool:
        settings = self.config.settings
        if not settings.postpone_if_active:
            return False
        try:
            idle = self._idle()
        except Exception:
            return False
        return idle is not None and idle < settings.idle_minutes * 60

    def _check_sessions(self, now: datetime) -> None:
        for launcher_id, session in list(self.state.sessions.items()):
            if self._stop.is_set():
                return
            launcher = self.config.launcher(launcher_id)
            if launcher is None:
                del self.state.sessions[launcher_id]
                self._save_state(self.state)
                continue
            if session.end > now:
                continue
            if self.procs.is_running(launcher) and self._user_is_active():
                session.end = now + POSTPONE_STEP
                if not session.postponed:
                    session.postponed = True
                    log.info(
                        "Закрытие %s отложено: за компьютером кто-то работает. "
                        "Лаунчер закроется после %d мин бездействия",
                        launcher.name, self.config.settings.idle_minutes,
                    )
                self._save_state(self.state)
                continue
            if self._close(launcher) is None:
                return  # программа завершается — сеанс закроем при следующем запуске
            self.state.sessions.pop(launcher_id, None)
            self._save_state(self.state)

    def _close(self, launcher: Launcher) -> bool | None:
        """Закрывает лаунчер: сначала мягко, затем принудительно.
        Возвращает None, если закрытие прервано выходом из программы."""
        if not self.procs.is_running(launcher):
            log.info("%s уже закрыт", launcher.name)
            return True
        log.info("Закрываю %s", launcher.name)
        try:
            self.procs.request_close(launcher)
        except Exception as exc:
            log.warning("Мягкое закрытие %s не удалось: %s", launcher.name, exc)
        deadline = time.monotonic() + max(0, self.config.settings.close_timeout_sec)
        while self.procs.is_running(launcher):
            if time.monotonic() >= deadline:
                break
            if self._stop.wait(0.5):
                return None
        else:
            log.info("%s закрыт", launcher.name)
            return True

        _, denied = self.procs.kill(launcher)
        for _ in range(6):
            if not self.procs.is_running(launcher):
                break
            if self._stop.wait(0.5):
                return None
        else:
            hint = " (нет прав — запустите программу от имени администратора)" if denied else ""
            log.error("Не удалось закрыть %s%s", launcher.name, hint)
            return False
        log.info("%s закрыт принудительно", launcher.name)
        return True
