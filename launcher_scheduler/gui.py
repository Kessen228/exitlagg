"""Окно программы (tkinter)."""

from __future__ import annotations

import copy
import logging
import os
import queue
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import autostart, presets
from .config import APP_NAME, APP_TITLE, Config, Launcher, Settings, Store, Task, new_id
from .instance import SingleInstance
from .processes import expand
from .schedule import DAY_NAMES, format_days, format_duration, format_moment, next_occurrence, parse_hhmm
from .scheduler import Scheduler

log = logging.getLogger("launcher_scheduler")

try:
    import pystray
    from PIL import Image
except Exception:  # значок в трее необязателен; без графической оболочки pystray может не загрузиться
    pystray = None

HELP_TEXT = """\
Как это работает

Программа в заданное время открывает выбранные лаунчеры (Steam, Epic Games, EA app и другие), \
чтобы они скачали обновления игр, и закрывает их, когда истечёт указанное время работы. \
Задачи выполняются, только пока программа запущена — при закрытии окна она продолжает работать \
в области уведомлений (значок рядом с часами).

Перед началом работы

1. Авторизуйтесь во всех лаунчерах, где это нужно. Откройте каждый лаунчер вручную, войдите \
в аккаунт и отметьте «Запомнить меня» / «Оставаться в системе». Иначе при запуске по расписанию \
лаунчер остановится на окне входа и ничего не скачает.

2. Включите автоматическое обновление игр в каждом лаунчере:
   • Steam — «Настройки → Загрузки»: уберите ограничение времени загрузок; в свойствах игры \
→ «Обновления» выберите автоматическое обновление.
   • Epic Games — «Настройки»: включите автообновления; в «Управлении» игрой включите автообновление.
   • EA app — «Настройки → Загрузка»: автоматические обновления игр.
   • Ubisoft Connect — «Настройки → Загрузки»: автоматическое обновление игр.
   • Battle.net — «Настройки → Загрузки»: автоматическое обновление игр.
   • GOG GALAXY — «Настройки → Установка и обновления»: автоматически обновлять игры.
   Названия пунктов могут немного отличаться в зависимости от версии лаунчера.

3. На вкладке «Настройки» включите «Запускать вместе с Windows». Компьютер в момент запуска \
задачи должен быть включён и не находиться в спящем режиме.

Настройка расписания

1. Вкладка «Лаунчеры» — нажмите «Найти установленные» или добавьте лаунчер вручную \
(«Добавить…», укажите путь к .exe).
2. Вкладка «Расписание» — «Добавить…»: время запуска, дни недели, сколько лаунчеры должны \
проработать и какие именно лаунчеры запускать.
3. Кнопка «Запустить сейчас» позволяет проверить задачу, не дожидаясь расписания.

Полезно знать

• Если лаунчер уже был открыт до начала задачи (например, вы сами им пользуетесь), \
программа его не закроет. Это можно отключить в настройках.
• Если в момент закрытия за компьютером кто-то работает или играет, закрытие откладывается, \
пока компьютер не освободится. Это тоже настраивается.
• Если компьютер был выключен в момент запуска, а время работы задачи ещё не вышло, \
задача запустится при старте программы.
• Сначала программа просит лаунчер закрыться штатно (Steam — командой -shutdown) \
и только если он не закрылся за отведённое время, завершает его принудительно.
• Все действия записываются на вкладку «Журнал».
"""


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "assets" if base else Path(__file__).resolve().parent.parent / "assets"


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sync_tree(tree: ttk.Treeview, rows: list[tuple[str, tuple]]) -> None:
    """Обновляет строки таблицы на месте, сохраняя выделение и прокрутку."""
    wanted = [iid for iid, _ in rows]
    for iid in set(tree.get_children()) - set(wanted):
        tree.delete(iid)
    for index, (iid, values) in enumerate(rows):
        values = tuple(str(v) for v in values)
        if tree.exists(iid):
            if tuple(str(v) for v in tree.item(iid, "values")) != values:
                tree.item(iid, values=values)
            if tree.index(iid) != index:
                tree.move(iid, "", index)
        else:
            tree.insert("", index, iid=iid, values=values)


def parse_int(value: str, low: int, high: int, label: str) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{label}: введите целое число") from None
    if not low <= number <= high:
        raise ValueError(f"{label}: допустимо от {low} до {high}")
    return number


class Dialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent)
        self.withdraw()
        self.parent = parent
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.body.columnconfigure(1, weight=1)
        self.bind("<Escape>", lambda _e: self.destroy())

    def add_buttons(self, row: int) -> None:
        bar = ttk.Frame(self.body)
        bar.grid(row=row, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(bar, text="OK", command=self.on_ok, default="active").pack(side=tk.RIGHT, padx=(0, 6))
        self.bind("<Return>", lambda _e: self.on_ok())

    def on_ok(self) -> None:
        raise NotImplementedError

    def warn(self, text: str) -> None:
        messagebox.showwarning(self.title(), text, parent=self)

    def run(self):
        self.update_idletasks()
        parent = self.parent.winfo_toplevel()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        if parent.winfo_viewable():
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        else:
            x = (self.winfo_screenwidth() - width) // 2
            y = (self.winfo_screenheight() - height) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result


class TaskDialog(Dialog):
    def __init__(self, parent, launchers: list[Launcher], task: Task | None):
        super().__init__(parent, "Изменить задачу" if task else "Новая задача")
        self.task = task or Task(launcher_ids=[launchers[0].id] if len(launchers) == 1 else [])
        self.launchers = launchers
        start = parse_hhmm(self.task.time)
        body = self.body

        ttk.Label(body, text="Время запуска:").grid(row=0, column=0, sticky="w", pady=4)
        clock = ttk.Frame(body)
        clock.grid(row=0, column=1, sticky="w")
        self.hour = tk.StringVar(value=f"{start.hour:02d}")
        self.minute = tk.StringVar(value=f"{start.minute:02d}")
        ttk.Spinbox(clock, from_=0, to=23, width=4, format="%02.0f", wrap=True,
                    textvariable=self.hour).pack(side=tk.LEFT)
        ttk.Label(clock, text=" : ").pack(side=tk.LEFT)
        ttk.Spinbox(clock, from_=0, to=59, width=4, format="%02.0f", wrap=True,
                    textvariable=self.minute).pack(side=tk.LEFT)

        ttk.Label(body, text="Дни недели:").grid(row=1, column=0, sticky="nw", pady=4)
        days = ttk.Frame(body)
        days.grid(row=1, column=1, sticky="w", pady=4)
        self.day_vars = [tk.BooleanVar(value=d in self.task.days) for d in range(7)]
        for day, name in enumerate(DAY_NAMES):
            ttk.Checkbutton(days, text=name, variable=self.day_vars[day]).grid(row=0, column=day, padx=(0, 6))
        quick = ttk.Frame(days)
        quick.grid(row=1, column=0, columnspan=7, sticky="w", pady=(4, 0))
        for text, selection in (("Каждый день", range(7)), ("Будни", range(5)), ("Выходные", (5, 6))):
            ttk.Button(quick, text=text, command=lambda s=selection: self._set_days(s)).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(body, text="Сколько работает:").grid(row=2, column=0, sticky="w", pady=4)
        duration = ttk.Frame(body)
        duration.grid(row=2, column=1, sticky="w", pady=4)
        hours, minutes = divmod(self.task.duration_min or 120, 60)
        self.dur_hours = tk.StringVar(value=str(hours))
        self.dur_minutes = tk.StringVar(value=str(minutes))
        self.dur_widgets = [
            ttk.Spinbox(duration, from_=0, to=99, width=4, textvariable=self.dur_hours),
            ttk.Label(duration, text="ч"),
            ttk.Spinbox(duration, from_=0, to=59, width=4, increment=5, textvariable=self.dur_minutes),
            ttk.Label(duration, text="мин"),
        ]
        for widget in self.dur_widgets:
            widget.pack(side=tk.LEFT, padx=(0, 4))
        self.unlimited = tk.BooleanVar(value=self.task.duration_min == 0)
        ttk.Checkbutton(body, text="Не закрывать лаунчеры автоматически", variable=self.unlimited,
                        command=self._toggle_unlimited).grid(row=3, column=1, sticky="w")
        self._toggle_unlimited()

        ttk.Label(body, text="Лаунчеры:").grid(row=4, column=0, sticky="nw", pady=(10, 4))
        box = ttk.Frame(body)
        box.grid(row=4, column=1, sticky="w", pady=(10, 4))
        self.launcher_vars: dict[str, tk.BooleanVar] = {}
        for index, launcher in enumerate(launchers):
            var = tk.BooleanVar(value=launcher.id in self.task.launcher_ids)
            self.launcher_vars[launcher.id] = var
            ttk.Checkbutton(box, text=launcher.name, variable=var).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 16), pady=1)

        self.enabled = tk.BooleanVar(value=self.task.enabled)
        ttk.Checkbutton(body, text="Задача включена", variable=self.enabled).grid(
            row=5, column=1, sticky="w", pady=(8, 0))
        self.add_buttons(6)

    def _set_days(self, selection) -> None:
        for day, var in enumerate(self.day_vars):
            var.set(day in selection)

    def _toggle_unlimited(self) -> None:
        state = "disabled" if self.unlimited.get() else "normal"
        for widget in self.dur_widgets:
            widget.configure(state=state)

    def on_ok(self) -> None:
        try:
            hour = parse_int(self.hour.get(), 0, 23, "Часы запуска")
            minute = parse_int(self.minute.get(), 0, 59, "Минуты запуска")
            duration = 0
            if not self.unlimited.get():
                duration = (parse_int(self.dur_hours.get(), 0, 99, "Часы работы") * 60
                            + parse_int(self.dur_minutes.get(), 0, 59, "Минуты работы"))
                if duration == 0:
                    raise ValueError("Укажите, сколько должны работать лаунчеры, "
                                     "или отметьте «Не закрывать лаунчеры автоматически»")
        except ValueError as exc:
            self.warn(str(exc))
            return
        days = [d for d, var in enumerate(self.day_vars) if var.get()]
        if not days:
            self.warn("Выберите хотя бы один день недели")
            return
        launcher_ids = [lid for lid, var in self.launcher_vars.items() if var.get()]
        if not launcher_ids:
            self.warn("Выберите хотя бы один лаунчер")
            return
        self.result = Task(
            launcher_ids=launcher_ids,
            time=f"{hour:02d}:{minute:02d}",
            days=days,
            duration_min=duration,
            enabled=self.enabled.get(),
            id=self.task.id,
        )
        self.destroy()


class LauncherDialog(Dialog):
    CUSTOM = "Свой лаунчер"

    def __init__(self, parent, launcher: Launcher | None):
        super().__init__(parent, "Изменить лаунчер" if launcher else "Новый лаунчер")
        self.launcher = launcher
        body = self.body
        row = 0
        if launcher is None:
            ttk.Label(body, text="Шаблон:").grid(row=row, column=0, sticky="w", pady=4)
            self.preset = tk.StringVar(value=self.CUSTOM)
            combo = ttk.Combobox(body, textvariable=self.preset, state="readonly", width=40,
                                 values=[self.CUSTOM] + [p.name for p in presets.PRESETS])
            combo.grid(row=row, column=1, columnspan=2, sticky="we", pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset())
            row += 1
        self.preset_key = launcher.preset if launcher else ""

        self.name = tk.StringVar(value=launcher.name if launcher else "")
        self.path = tk.StringVar(value=launcher.path if launcher else "")
        self.args = tk.StringVar(value=launcher.args if launcher else "")
        self.processes = tk.StringVar(value=", ".join(launcher.process_names) if launcher else "")
        self.close_args = tk.StringVar(value=launcher.close_args if launcher else "")

        ttk.Label(body, text="Название:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.name, width=48).grid(row=row, column=1, columnspan=2, sticky="we")
        row += 1
        ttk.Label(body, text="Программа:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.path, width=48).grid(row=row, column=1, sticky="we")
        ttk.Button(body, text="Обзор…", command=self._browse).grid(row=row, column=2, padx=(6, 0))
        row += 1
        ttk.Label(body, text="Параметры запуска:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.args).grid(row=row, column=1, columnspan=2, sticky="we")
        row += 1
        ttk.Label(body, text="Например, -silent — запуск свёрнутым в трей (поддерживают Steam и Epic Games).",
                  style="Hint.TLabel").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1
        ttk.Label(body, text="Процессы:").grid(row=row, column=0, sticky="w", pady=(10, 4))
        ttk.Entry(body, textvariable=self.processes).grid(row=row, column=1, columnspan=2, sticky="we",
                                                          pady=(10, 4))
        row += 1
        ttk.Label(body, text="Имена процессов через запятую, по которым программа узнаёт, что лаунчер\n"
                             "запущен, и закрывает его. Можно указать полный путь к .exe.",
                  style="Hint.TLabel").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1
        ttk.Label(body, text="Команда закрытия:").grid(row=row, column=0, sticky="w", pady=(10, 4))
        ttk.Entry(body, textvariable=self.close_args).grid(row=row, column=1, columnspan=2, sticky="we",
                                                           pady=(10, 4))
        row += 1
        ttk.Label(body, text="Необязательно. Параметры для штатного закрытия (у Steam: -shutdown).\n"
                             "Если пусто — лаунчер закрывается через завершение процессов.",
                  style="Hint.TLabel").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1
        self.add_buttons(row)

    def _apply_preset(self) -> None:
        preset = next((p for p in presets.PRESETS if p.name == self.preset.get()), None)
        if preset is None:
            self.preset_key = ""
            return
        path = presets.find_path(preset) or ""
        template = presets.make_launcher(preset, path or preset.exe)
        self.preset_key = preset.key
        self.name.set(template.name)
        self.path.set(path)
        self.args.set(template.args)
        self.processes.set(", ".join(template.process_names) if path else ", ".join(
            p for p in template.process_names if "\\" not in p and "/" not in p))
        self.close_args.set(template.close_args)
        if not path:
            self.warn(f"{preset.name} не найден на этом компьютере. Укажите путь к программе вручную.")

    def _browse(self) -> None:
        current = expand(self.path.get())
        path = filedialog.askopenfilename(
            parent=self,
            title="Выберите программу лаунчера",
            initialdir=os.path.dirname(current) if current else None,
            filetypes=[("Программы", "*.exe"), ("Ярлыки", "*.lnk"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        path = os.path.normpath(path)
        self.path.set(path)
        base = os.path.basename(path)
        if not self.name.get().strip():
            self.name.set(os.path.splitext(base)[0])
        if not self.processes.get().strip() and base.lower().endswith(".exe"):
            self.processes.set(base)

    def on_ok(self) -> None:
        name = self.name.get().strip()
        path = self.path.get().strip().strip('"')
        if not name:
            self.warn("Укажите название лаунчера")
            return
        if not path:
            self.warn("Укажите путь к программе лаунчера")
            return
        if not os.path.isfile(expand(path)) and not messagebox.askyesno(
                self.title(), "Файл не найден. Всё равно сохранить?", parent=self):
            return
        processes = [p.strip() for p in self.processes.get().replace(";", ",").split(",") if p.strip()]
        if not processes and not path.lower().endswith(".exe") and not messagebox.askyesno(
                self.title(), "Не указаны процессы лаунчера. Программа не сможет понять, запущен ли он, "
                              "и не сможет закрыть его по расписанию.\n\nВсё равно сохранить?", parent=self):
            return
        self.result = Launcher(
            name=name,
            path=path,
            args=self.args.get().strip(),
            process_names=processes,
            close_args=self.close_args.get().strip(),
            preset=self.preset_key,
            id=self.launcher.id if self.launcher else new_id(),
        )
        self.destroy()


class Tray:
    def __init__(self, image_path: Path, commands: queue.Queue):
        image = Image.open(image_path)
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda: commands.put("show"), default=True),
            pystray.MenuItem("Выход", lambda: commands.put("quit")),
        )
        self.icon = pystray.Icon(APP_NAME, image, APP_TITLE, menu)
        self.icon.run_detached()

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text, APP_TITLE)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass


class MainWindow:
    REFRESH_MS = 2000
    POLL_MS = 300

    def __init__(self, root: tk.Tk, store: Store, config: Config, scheduler: Scheduler,
                 instance: SingleInstance, log_queue: queue.Queue, minimized: bool = False,
                 welcome: list[Launcher] | None = None):
        self.root = root
        self.store = store
        self.config = config
        self.scheduler = scheduler
        self.instance = instance
        self.log_queue = log_queue
        self.commands: queue.Queue = queue.Queue()
        self.tray: Tray | None = None
        self.tray_hint_shown = False

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        root.title(APP_TITLE)
        root.geometry(f"{self._px(940)}x{self._px(600)}")
        root.minsize(self._px(760), self._px(480))
        style = ttk.Style(root)
        style.configure("Treeview", rowheight=self._px(24))
        style.configure("Hint.TLabel", foreground="#6b6b6b")
        self._set_icon()

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))
        self._build_tasks_tab()
        self._build_launchers_tab()
        self._build_log_tab()
        self._build_settings_tab()
        self._build_help_tab()

        status = ttk.Frame(root, padding=(10, 4))
        status.pack(fill=tk.X)
        self.status_next = ttk.Label(status)
        self.status_next.pack(anchor="w")
        self.status_active = ttk.Label(status, style="Hint.TLabel")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if pystray is not None:
            try:
                self.tray = Tray(resource_dir() / "icon.png", self.commands)
            except Exception as exc:
                log.warning("Значок в трее недоступен: %s", exc)

        self.refresh()
        self._poll()
        self._schedule_refresh()
        if minimized:
            self.hide()
        if welcome is not None:
            root.after(400, lambda: self._show_welcome(welcome))

    def _px(self, value: int) -> int:
        return int(value * self.scale)

    def _set_icon(self) -> None:
        icon = resource_dir() / "icon.png"
        if icon.exists():
            try:
                self._icon_image = tk.PhotoImage(file=str(icon))
                self.root.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass

    # ---- построение вкладок ----

    def _make_tree(self, parent, columns) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for key, title, width, stretch in columns:
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=self._px(width), minwidth=self._px(40), stretch=stretch, anchor="w")
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        return tree

    @staticmethod
    def _button_bar(parent, buttons) -> ttk.Frame:
        bar = ttk.Frame(parent)
        bar.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        for item in buttons:
            if item is None:
                ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
                continue
            text, command = item
            ttk.Button(bar, text=text, command=command).pack(side=tk.LEFT, padx=(0, 6))
        return bar

    def _build_tasks_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Расписание")
        self.task_tree = self._make_tree(frame, (
            ("enabled", "Вкл.", 50, False),
            ("time", "Время", 70, False),
            ("days", "Дни", 170, False),
            ("duration", "Работает", 110, False),
            ("launchers", "Лаунчеры", 260, True),
            ("next", "Следующий запуск", 160, False),
        ))
        self.task_tree.bind("<Double-1>", lambda _e: self.edit_task())
        self.task_tree.bind("<Delete>", lambda _e: self.delete_task())
        self._button_bar(frame, (
            ("Добавить…", self.add_task),
            ("Изменить…", self.edit_task),
            ("Удалить", self.delete_task),
            ("Вкл. / выкл.", self.toggle_task),
            None,
            ("Запустить сейчас", self.run_task_now),
        ))

    def _build_launchers_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Лаунчеры")
        self.launcher_tree = self._make_tree(frame, (
            ("name", "Название", 200, False),
            ("status", "Состояние", 250, False),
            ("path", "Программа", 380, True),
        ))
        self.launcher_tree.bind("<Double-1>", lambda _e: self.edit_launcher())
        self.launcher_tree.bind("<Delete>", lambda _e: self.delete_launcher())
        self._button_bar(frame, (
            ("Добавить…", self.add_launcher),
            ("Изменить…", self.edit_launcher),
            ("Удалить", self.delete_launcher),
            ("Найти установленные", self.detect_launchers),
            None,
            ("Запустить", self.launch_selected),
            ("Закрыть", self.close_selected),
        ))

    def _build_log_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Журнал")
        self.log_text = ScrolledText(frame, wrap=tk.WORD, height=10, state=tk.DISABLED, relief=tk.FLAT,
                                     borderwidth=1, highlightthickness=1, font="TkDefaultFont")
        self.log_text.grid(row=0, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log_text.tag_configure("error", foreground="#b3261e")
        self.log_text.tag_configure("warning", foreground="#8a5a00")
        self._button_bar(frame, (
            ("Очистить", self._clear_log),
            ("Открыть папку программы", lambda: open_folder(self.store.directory)),
        ))

    def _build_settings_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(frame, text="Настройки")
        settings = self.config.settings
        self.var_autostart = tk.BooleanVar(value=autostart.is_enabled())
        self.var_keep = tk.BooleanVar(value=settings.keep_preexisting)
        self.var_catch_up = tk.BooleanVar(value=settings.catch_up_missed)
        self.var_postpone = tk.BooleanVar(value=settings.postpone_if_active)
        self.var_idle = tk.StringVar(value=str(settings.idle_minutes))
        self.var_interval = tk.StringVar(value=str(settings.launch_interval_sec))
        self.var_timeout = tk.StringVar(value=str(settings.close_timeout_sec))

        wrap = self._px(640)
        row = 0

        def check(text, var, hint=None, **kwargs):
            nonlocal row
            ttk.Checkbutton(frame, text=text, variable=var, **kwargs).grid(row=row, column=0, columnspan=3,
                                                                          sticky="w", pady=(8, 0))
            row += 1
            if hint:
                ttk.Label(frame, text=hint, style="Hint.TLabel", wraplength=wrap).grid(
                    row=row, column=0, columnspan=3, sticky="w", padx=(24, 0))
                row += 1

        def number(text, var, low, high, suffix):
            nonlocal row
            ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", pady=(10, 0), padx=(24, 0))
            ttk.Spinbox(frame, from_=low, to=high, width=5, textvariable=var).grid(
                row=row, column=1, sticky="w", pady=(10, 0), padx=6)
            ttk.Label(frame, text=suffix).grid(row=row, column=2, sticky="w", pady=(10, 0))
            row += 1

        check("Запускать вместе с Windows", self.var_autostart,
              "Программа стартует свёрнутой при входе в систему. Без этого задачи не выполнятся, "
              "пока программу не откроют вручную.",
              state="normal" if autostart.supported() else "disabled")
        check("Не закрывать лаунчеры, которые были открыты до начала задачи", self.var_keep,
              "Если вы сами открыли лаунчер, программа не закроет его по расписанию.")
        check("Запускать пропущенные задачи", self.var_catch_up,
              "Если компьютер или программа были выключены в момент запуска, а время работы задачи "
              "ещё не вышло, лаунчеры откроются сразу после старта программы.")
        check("Откладывать закрытие, пока за компьютером кто-то работает", self.var_postpone,
              "Лаунчеры закроются, только когда клавиатурой и мышью не пользуются указанное время — "
              "так программа не помешает игре.")
        number("Компьютер свободен после", self.var_idle, 1, 120, "мин без активности")
        number("Пауза между запуском лаунчеров", self.var_interval, 0, 300, "сек")
        number("Ждать штатного закрытия лаунчера", self.var_timeout, 0, 600, "сек, затем закрыть принудительно")

        bar = ttk.Frame(frame)
        bar.grid(row=row, column=0, columnspan=3, sticky="w", pady=(18, 0))
        ttk.Button(bar, text="Сохранить", command=self.save_settings).pack(side=tk.LEFT)
        self.settings_saved = ttk.Label(bar, style="Hint.TLabel")
        self.settings_saved.pack(side=tk.LEFT, padx=10)

    def _build_help_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Справка")
        text = ScrolledText(frame, wrap=tk.WORD, relief=tk.FLAT, borderwidth=0, padx=8, pady=6,
                            font="TkDefaultFont")
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", HELP_TEXT)
        headings = ("Как это работает", "Перед началом работы", "Настройка расписания", "Полезно знать")
        self._heading_font = tkfont.nametofont("TkDefaultFont").copy()
        size = self._heading_font.cget("size")  # отрицательный размер задан в пикселях
        self._heading_font.configure(weight="bold", size=size + 2 if size > 0 else size - 3)
        text.tag_configure("heading", font=self._heading_font, spacing1=6, spacing3=4)
        for heading in headings:
            start = text.search(heading, "1.0", tk.END)
            if start:
                text.tag_add("heading", start, f"{start} lineend")
        text.configure(state=tk.DISABLED)

    # ---- обновление данных ----

    def refresh(self) -> None:
        now = datetime.now()
        running, sessions = self.scheduler.snapshot()
        names = {l.id: l.name for l in self.config.launchers}

        task_rows = []
        upcoming = []
        for task in sorted(self.config.tasks, key=lambda t: t.time):
            launcher_names = ", ".join(names[i] for i in task.launcher_ids if i in names) or "—"
            next_text = "выключена"
            if task.enabled:
                moment = next_occurrence(task.time, task.days, now) if task.days else None
                next_text = format_moment(moment, now.date()) if moment else "—"
                if moment and launcher_names != "—":
                    upcoming.append((moment, launcher_names))
            task_rows.append((task.id, (
                "да" if task.enabled else "нет", task.time, format_days(task.days),
                format_duration(task.duration_min), launcher_names, next_text,
            )))
        sync_tree(self.task_tree, task_rows)

        launcher_rows = []
        for launcher in self.config.launchers:
            if not os.path.isfile(expand(launcher.path)):
                status = "файл не найден"
            elif running.get(launcher.id):
                session = sessions.get(launcher.id)
                status = "запущен"
                if session:
                    when = (f"в {session.end:%H:%M}" if session.end.date() == now.date()
                            else format_moment(session.end, now.date()))
                    status = f"запущен · закроется {when}"
            else:
                status = "не запущен"
            launcher_rows.append((launcher.id, (launcher.name, status, launcher.path)))
        sync_tree(self.launcher_tree, launcher_rows)

        if upcoming:
            moment, launcher_names = min(upcoming)
            self.status_next.configure(text=f"Следующий запуск: {format_moment(moment, now.date())} — {launcher_names}")
        else:
            self.status_next.configure(text="Нет запланированных задач")
        active = [f"{names[k]} до {s.end:%H:%M}" for k, s in sessions.items() if k in names]
        if active:
            self.status_active.configure(text="Работают по расписанию: " + ", ".join(active))
            if not self.status_active.winfo_ismapped():
                self.status_active.pack(anchor="w")
        else:
            self.status_active.pack_forget()

    def _schedule_refresh(self) -> None:
        try:
            self.refresh()
        finally:
            self.root.after(self.REFRESH_MS, self._schedule_refresh)

    def _poll(self) -> None:
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self._append_log(lines)
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            if command == "show":
                self.show()
            elif command == "quit":
                self.quit()
                return
        if self.instance.show_requested():
            self.show()
        self.root.after(self.POLL_MS, self._poll)

    def _append_log(self, lines) -> None:
        at_end = self.log_text.yview()[1] >= 0.999
        self.log_text.configure(state=tk.NORMAL)
        for level, text in lines:
            tag = "error" if level >= logging.ERROR else "warning" if level >= logging.WARNING else ""
            self.log_text.insert(tk.END, text + "\n", tag)
        excess = int(self.log_text.index("end-1c").split(".")[0]) - 2000
        if excess > 0:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.configure(state=tk.DISABLED)
        if at_end:
            self.log_text.see(tk.END)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def commit(self, changed_tasks=()) -> None:
        try:
            self.store.save_config(self.config)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Не удалось сохранить настройки:\n{exc}", parent=self.root)
        self.scheduler.update_config(copy.deepcopy(self.config), changed_tasks)
        self.refresh()

    # ---- задачи ----

    def _selected(self, tree: ttk.Treeview) -> str | None:
        selection = tree.selection()
        return selection[0] if selection else None

    def add_task(self) -> None:
        if not self.config.launchers:
            messagebox.showinfo(APP_TITLE, "Сначала добавьте лаунчеры на вкладке «Лаунчеры».", parent=self.root)
            self.notebook.select(1)
            return
        task = TaskDialog(self.root, self.config.launchers, None).run()
        if task:
            self.config.tasks.append(task)
            self.commit([task.id])
            self.task_tree.selection_set(task.id)

    def edit_task(self) -> None:
        task_id = self._selected(self.task_tree)
        task = self.config.task(task_id) if task_id else None
        if task is None:
            return
        updated = TaskDialog(self.root, self.config.launchers, task).run()
        if updated:
            self.config.tasks[self.config.tasks.index(task)] = updated
            self.commit([updated.id])

    def delete_task(self) -> None:
        task_id = self._selected(self.task_tree)
        task = self.config.task(task_id) if task_id else None
        if task and messagebox.askyesno(APP_TITLE, "Удалить выбранную задачу?", parent=self.root):
            self.config.tasks.remove(task)
            self.commit([task.id])

    def toggle_task(self) -> None:
        task_id = self._selected(self.task_tree)
        task = self.config.task(task_id) if task_id else None
        if task:
            task.enabled = not task.enabled
            self.commit([task.id])

    def run_task_now(self) -> None:
        task_id = self._selected(self.task_tree)
        if not task_id:
            messagebox.showinfo(APP_TITLE, "Выберите задачу в списке.", parent=self.root)
            return
        self.scheduler.run_task_now(task_id)

    # ---- лаунчеры ----

    def add_launcher(self) -> None:
        launcher = LauncherDialog(self.root, None).run()
        if launcher:
            self.config.launchers.append(launcher)
            self.commit()
            self.launcher_tree.selection_set(launcher.id)

    def edit_launcher(self) -> None:
        launcher_id = self._selected(self.launcher_tree)
        launcher = self.config.launcher(launcher_id) if launcher_id else None
        if launcher is None:
            return
        updated = LauncherDialog(self.root, launcher).run()
        if updated:
            self.config.launchers[self.config.launchers.index(launcher)] = updated
            self.commit()

    def delete_launcher(self) -> None:
        launcher_id = self._selected(self.launcher_tree)
        launcher = self.config.launcher(launcher_id) if launcher_id else None
        if launcher is None:
            return
        used = [t for t in self.config.tasks if launcher.id in t.launcher_ids]
        question = f"Удалить «{launcher.name}»?"
        if used:
            question += f"\n\nОн будет убран из задач расписания ({len(used)})."
        if not messagebox.askyesno(APP_TITLE, question, parent=self.root):
            return
        self.config.launchers.remove(launcher)
        for task in used:
            task.launcher_ids.remove(launcher.id)
        self.commit([t.id for t in used])

    def detect_launchers(self) -> None:
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            found = presets.detect_installed(self.config.launchers)
        finally:
            self.root.configure(cursor="")
        if not found:
            messagebox.showinfo(APP_TITLE, "Новых лаунчеров не найдено.\n\n"
                                           "Добавьте лаунчер вручную кнопкой «Добавить…».", parent=self.root)
            return
        self.config.launchers.extend(found)
        self.commit()
        messagebox.showinfo(APP_TITLE, "Добавлены:\n" + "\n".join(f"• {l.name}" for l in found), parent=self.root)

    def launch_selected(self) -> None:
        launcher_id = self._selected(self.launcher_tree)
        if launcher_id:
            self.scheduler.launch_now(launcher_id)

    def close_selected(self) -> None:
        launcher_id = self._selected(self.launcher_tree)
        if launcher_id:
            self.scheduler.close_now(launcher_id)

    # ---- настройки ----

    def save_settings(self) -> None:
        try:
            settings = Settings(
                keep_preexisting=self.var_keep.get(),
                catch_up_missed=self.var_catch_up.get(),
                postpone_if_active=self.var_postpone.get(),
                idle_minutes=parse_int(self.var_idle.get(), 1, 120, "Время бездействия"),
                launch_interval_sec=parse_int(self.var_interval.get(), 0, 300, "Пауза между запусками"),
                close_timeout_sec=parse_int(self.var_timeout.get(), 0, 600, "Ожидание закрытия"),
            )
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            return
        if autostart.supported() and self.var_autostart.get() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.var_autostart.get())
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Не удалось изменить автозапуск:\n{exc}", parent=self.root)
                self.var_autostart.set(autostart.is_enabled())
        self.config.settings = settings
        self.commit()
        self.settings_saved.configure(text="Сохранено")
        self.root.after(3000, lambda: self.settings_saved.configure(text=""))

    # ---- окно ----

    def _show_welcome(self, found: list[Launcher]) -> None:
        self.show()
        found_text = ("Найдены лаунчеры: " + ", ".join(l.name for l in found) + "."
                      if found else "Установленные лаунчеры не найдены — добавьте их вручную на вкладке «Лаунчеры».")
        messagebox.showinfo(
            APP_TITLE,
            f"{found_text}\n\n"
            "Перед использованием:\n"
            "1. Авторизуйтесь во всех лаунчерах, где это нужно (вход с «Запомнить меня»).\n"
            "2. Включите в лаунчерах автоматическое обновление игр.\n"
            "3. Включите «Запускать вместе с Windows» на вкладке «Настройки».\n\n"
            "Подробности — на вкладке «Справка».",
            parent=self.root,
        )
        self.notebook.select(4)

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def on_close(self) -> None:
        if self.tray:
            self.hide()
            if not self.tray_hint_shown:
                self.tray_hint_shown = True
                self.tray.notify("Программа продолжает работать в фоне. "
                                 "Чтобы выйти, щёлкните значок правой кнопкой мыши → «Выход».")
            return
        if messagebox.askyesno(APP_TITLE, "Пока программа закрыта, задачи по расписанию не выполняются.\n\n"
                                          "Выйти? (Нет — свернуть окно)", parent=self.root):
            self.quit()
        else:
            self.root.iconify()

    def quit(self) -> None:
        self.scheduler.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()
