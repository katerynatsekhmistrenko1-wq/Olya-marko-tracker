"""Додавання готівкової витрати в manual.beancount.

Запис іде атомарно (тимчасовий файл + os.replace) під блокуванням, після
запису облік перечитується. Якщо з'явилися нові помилки — файл повертається
до попереднього стану, і користувач бачить повідомлення замість зламаного обліку.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import finance

try:  # на Linux (PythonAnywhere) блокуємо і між процесами
    import fcntl
except ImportError:  # pragma: no cover - Windows для локальної розробки
    fcntl = None

_lock = threading.Lock()
MAX_AMOUNT = Decimal("100000")
INSERT_MARKER = '"insert-entry"'


class EntryError(ValueError):
    """Повідомлення для людини: що не так у формі."""


@dataclass(frozen=True)
class CashExpense:
    date: dt.date
    who: str
    category: str
    amount: Decimal
    note: str
    shared: bool
    added_by: str

    def to_beancount(self) -> str:
        tags = " #" + finance.TAG_CASH + (" #" + finance.TAG_SHARED if self.shared else "")
        return (
            f'{self.date.isoformat()} * "{self.note}"{tags}\n'
            f'  added_by: "{self.added_by}"\n'
            f"  Expenses:{self.category}:{self.who}  {self.amount:.2f} PLN\n"
            f"  Assets:Готівка:{self.who}\n"
        )


def parse_form(form, user: str, today: dt.date | None = None) -> CashExpense:
    """Перевіряє поля форми. Кидає EntryError з текстом для людини."""
    today = today or dt.date.today()

    who = (form.get("who") or "").strip()
    if who not in finance.PEOPLE:
        raise EntryError("Оберіть, чия це витрата.")

    raw_amount = (form.get("amount") or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        raise EntryError("Сума має бути числом, наприклад 12,50.") from None
    if not amount.is_finite() or amount <= 0:
        raise EntryError("Сума має бути більшою за нуль.")
    if amount > MAX_AMOUNT:
        raise EntryError("Завелика сума для готівки. Перевірте, чи немає зайвих нулів.")
    if amount != amount.quantize(Decimal("0.01")):
        raise EntryError("Не більше двох знаків після коми.")

    try:
        date = dt.date.fromisoformat((form.get("date") or "").strip())
    except ValueError:
        raise EntryError("Дата у форматі РРРР-ММ-ДД.") from None
    if date > today + dt.timedelta(days=1):
        raise EntryError("Дата в майбутньому.")
    if date < dt.date(2026, 1, 1):
        raise EntryError("Облік починається з 2026 року.")

    category = (form.get("category") or "").strip()
    if category not in finance.CASH_CATEGORIES:
        raise EntryError("Оберіть категорію зі списку.")

    note = " ".join((form.get("note") or "").split())
    note = re.sub(r'["\\]', "'", note)[:80]
    if not note:
        note = f"Готівка: {category.replace('-', ' ').lower()}"

    shared = form.get("shared") in ("1", "on", "true")
    return CashExpense(date, who, category, amount, note, shared, user)


@contextmanager
def _file_lock(path: Path):
    with _lock:
        lock_path = path.with_suffix(path.suffix + ".lock")
        with open(lock_path, "a") as lf:
            if fcntl:
                fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(lf, fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".manual-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _insert(text: str, block: str) -> str:
    """Вставляє запис перед рядком insert-entry (туди ж пише і Fava), інакше в кінець."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if INSERT_MARKER in line and not line.lstrip().startswith(";"):
            return "".join(lines[:i]) + block + "\n" + "".join(lines[i:])
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block


def append(expense: CashExpense, ledger_path: Path = finance.LEDGER,
           manual_path: Path = finance.MANUAL) -> None:
    with _file_lock(manual_path):
        before = finance.load(ledger_path, fresh=True)
        original = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
        _atomic_write(manual_path, _insert(original, expense.to_beancount()))
        after = finance.load(ledger_path, fresh=True)
        if len(after.errors) > len(before.errors):
            _atomic_write(manual_path, original)
            finance.load(ledger_path, fresh=True)
            raise EntryError("Облік не прийняв цей запис, нічого не збережено. "
                             "Перевірте дату й суму або напишіть Олі.")
