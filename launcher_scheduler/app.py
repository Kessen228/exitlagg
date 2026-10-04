"""Точка входа: настройка журнала, загрузка настроек, запуск планировщика и окна."""

from __future__ import annotations

import argparse
import copy
import logging
import queue
import sys
import tkinter as tk
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

from . import __version__, autostart, presets
from .config import APP_TITLE, Store, data_dir
from .gui import MainWindow
from .instance import SingleInstance
from .processes import ProcessManager
from .scheduler import Scheduler

log = logging.getLogger("launcher_scheduler")


class QueueHandler(logging.Handler):
    """Передаёт записи журнала в окно программы."""

    def __init__(self, target: queue.Queue):
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.put((record.levelno, self.format(record)))
        except Exception:
            self.handleError(record)


def setup_logging(directory, ui_queue: queue.Queue) -> None:
    log.setLevel(logging.INFO)
    log.propagate = False
    file_handler = RotatingFileHandler(directory / "app.log", maxBytes=512 * 1024, backupCount=2,
                                       encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    ui_handler = QueueHandler(ui_queue)
    ui_handler.setFormatter(logging.Formatter("%(asctime)s   %(message)s", datefmt="%d.%m %H:%M:%S"))
    log.addHandler(ui_handler)


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--minimized", action="store_true", help="запустить свёрнутой (для автозапуска)")
    args = parser.parse_args(argv)

    directory = data_dir()
    instance = SingleInstance(directory)
    if not instance.acquire():
        instance.request_show()
        return 0

    ui_queue: queue.Queue = queue.Queue()
    setup_logging(directory, ui_queue)
    log.info("%s %s запущен", APP_TITLE, __version__)

    store = Store(directory)
    config, first_run = store.load_config()
    welcome = None
    if first_run:
        welcome = presets.detect_installed(config.launchers)
        config.launchers.extend(welcome)
        store.save_config(config)
        if welcome:
            log.info("Найдены лаунчеры: %s", ", ".join(l.name for l in welcome))

    try:
        autostart.refresh()
    except OSError as exc:
        log.warning("Не удалось обновить автозапуск: %s", exc)

    scheduler = Scheduler(copy.deepcopy(config), store.load_state(), ProcessManager(), save_state=store.save_state)

    enable_dpi_awareness()
    root = tk.Tk()
    try:
        MainWindow(root, store, config, scheduler, instance, ui_queue,
                   minimized=args.minimized, welcome=welcome)
    except Exception:
        log.exception("Не удалось открыть окно программы")
        messagebox.showerror(APP_TITLE, "Не удалось открыть окно программы. Подробности в app.log.")
        return 1
    scheduler.start()
    root.mainloop()
    scheduler.stop()
    log.info("Программа закрыта")
    return 0
