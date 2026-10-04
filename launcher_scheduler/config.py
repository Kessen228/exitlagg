"""Модели данных и их хранение на диске (config.json и state.json)."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "LauncherScheduler"
APP_TITLE = "Планировщик лаунчеров"

log = logging.getLogger("launcher_scheduler")


def data_dir() -> Path:
    """Папка для настроек, состояния и журнала."""
    override = os.environ.get("LAUNCHER_SCHEDULER_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "launcher-scheduler"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Launcher:
    name: str
    path: str
    args: str = ""
    # Имена процессов (или полные пути к exe), по которым лаунчер определяется и закрывается.
    process_names: list[str] = field(default_factory=list)
    # Параметры для корректного закрытия, например "-shutdown" у Steam.
    close_args: str = ""
    preset: str = ""
    id: str = field(default_factory=new_id)

    def process_specs(self) -> list[str]:
        if self.process_names:
            return list(self.process_names)
        base = os.path.basename(self.path.replace("\\", "/"))
        return [base] if base.lower().endswith(".exe") else []


@dataclass
class Task:
    launcher_ids: list[str]
    time: str = "04:00"
    days: list[int] = field(default_factory=lambda: list(range(7)))  # 0 = понедельник
    duration_min: int = 120  # 0 = не закрывать автоматически
    enabled: bool = True
    id: str = field(default_factory=new_id)


@dataclass
class Settings:
    keep_preexisting: bool = True
    catch_up_missed: bool = True
    postpone_if_active: bool = True
    idle_minutes: int = 5
    launch_interval_sec: int = 5
    close_timeout_sec: int = 15


@dataclass
class Config:
    launchers: list[Launcher] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    def launcher(self, launcher_id: str) -> Launcher | None:
        return next((item for item in self.launchers if item.id == launcher_id), None)

    def task(self, task_id: str) -> Task | None:
        return next((item for item in self.tasks if item.id == task_id), None)


@dataclass
class Session:
    """Лаунчер, запущенный по расписанию, который нужно закрыть в момент `end`."""

    launcher_id: str
    end: datetime
    started: datetime
    task_id: str = ""
    postponed: bool = False


@dataclass
class State:
    sessions: dict[str, Session] = field(default_factory=dict)
    # Последний обработанный запуск каждой задачи, чтобы не запускать её повторно.
    handled: dict[str, datetime] = field(default_factory=dict)


def _build(cls, data):
    if not isinstance(data, dict):
        raise TypeError(f"ожидался объект для {cls.__name__}")
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in names})


def _build_list(cls, items) -> list:
    result = []
    for item in items or []:
        try:
            result.append(_build(cls, item))
        except (TypeError, ValueError) as exc:
            log.warning("Пропущена повреждённая запись в настройках: %s", exc)
    return result


def config_from_dict(data: dict) -> Config:
    settings = Settings()
    try:
        settings = _build(Settings, data.get("settings") or {})
    except TypeError as exc:
        log.warning("Настройки повреждены, используются значения по умолчанию: %s", exc)
    return Config(
        launchers=_build_list(Launcher, data.get("launchers")),
        tasks=_build_list(Task, data.get("tasks")),
        settings=settings,
    )


def config_to_dict(config: Config) -> dict:
    return dataclasses.asdict(config)


def state_from_dict(data: dict) -> State:
    state = State()
    for launcher_id, raw in (data.get("sessions") or {}).items():
        try:
            state.sessions[launcher_id] = Session(
                launcher_id=launcher_id,
                end=datetime.fromisoformat(raw["end"]),
                started=datetime.fromisoformat(raw["started"]),
                task_id=raw.get("task_id", ""),
                postponed=bool(raw.get("postponed", False)),
            )
        except (KeyError, TypeError, ValueError):
            continue
    for task_id, raw in (data.get("handled") or {}).items():
        try:
            state.handled[task_id] = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
    return state


def state_to_dict(state: State) -> dict:
    return {
        "sessions": {
            launcher_id: {
                "end": session.end.isoformat(),
                "started": session.started.isoformat(),
                "task_id": session.task_id,
                "postponed": session.postponed,
            }
            for launcher_id, session in state.sessions.items()
        },
        "handled": {task_id: moment.isoformat() for task_id, moment in state.handled.items()},
    }


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    """Читает JSON; повреждённый файл переименовывает в *.bad и возвращает None."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("корневой элемент не является объектом")
        return data
    except (OSError, ValueError) as exc:
        log.error("Не удалось прочитать %s (%s), файл сохранён как %s.bad", path.name, exc, path.name)
        try:
            os.replace(path, path.with_suffix(path.suffix + ".bad"))
        except OSError:
            pass
        return None


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.config_path = directory / "config.json"
        self.state_path = directory / "state.json"

    def load_config(self) -> tuple[Config, bool]:
        """Возвращает настройки и признак первого запуска."""
        if not self.config_path.exists():
            return Config(), True
        data = _read_json(self.config_path)
        return (config_from_dict(data) if data is not None else Config()), False

    def save_config(self, config: Config) -> None:
        _write_json(self.config_path, config_to_dict(config))

    def load_state(self) -> State:
        data = _read_json(self.state_path)
        return state_from_dict(data) if data else State()

    def save_state(self, state: State) -> None:
        try:
            _write_json(self.state_path, state_to_dict(state))
        except OSError as exc:
            log.error("Не удалось сохранить состояние: %s", exc)
