"""Імпорт виписок Олі й Марка: data/*.csv -> transactions-*.beancount.

Запуск: uv run python import.py
Після імпорту: uv run python check.py
"""

import sys

from tracker.importer import StatementError, run

if __name__ == "__main__":
    try:
        run()
    except StatementError as e:
        sys.exit(f"Помилка імпорту: {e}")
