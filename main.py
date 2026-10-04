"""Запуск программы: python main.py (или pythonw main.py — без окна консоли)."""

import sys

from launcher_scheduler.app import main

if __name__ == "__main__":
    sys.exit(main())
