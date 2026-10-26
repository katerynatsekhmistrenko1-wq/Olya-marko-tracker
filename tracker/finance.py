"""Підрахунки для звіту і для check.py — з одного місця, щоб цифри не розходилися.

Звіт відповідає на три питання щомісяця для кожного (Оля, Марко, разом):
витрати за категоріями, норма заощаджень, фіксовані й змінні.
Плюс четверте, спільне: хто кому скільки винен за оренду й продукти.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = Path(os.environ.get("TRACKER_LEDGER", ROOT / "main.beancount"))
MANUAL = LEDGER.parent / "manual.beancount"

PEOPLE = ("Оля", "Марко")
TOGETHER = "Разом"
DATIVE = {"Оля": "Олі", "Марко": "Марку"}
TAG_FIXED = "fixed"
TAG_SHARED = "shared"
TAG_CASH = "cash"

ZERO = Decimal("0")

# Категорії, які можна обрати у формі готівки (без службових).
CASH_CATEGORIES = (
    "Продукти", "Кафе", "Доставка-Їжі", "Транспорт", "Покупки", "Розваги",
    "Догляд", "Спорт", "Житло", "Комунальні", "Звязок", "Підписки", "Без-Категорії",
)


# --------------------------------------------------------------------------
# Завантаження обліку (з кешем за часом зміни файлів)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Ledger:
    entries: list
    errors: list
    files: tuple[str, ...]


_cache: dict[str, tuple[tuple, Ledger]] = {}
_cache_lock = threading.Lock()


def _stamp(files) -> tuple:
    out = []
    for name in files:
        try:
            st = os.stat(name)
            out.append((name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            out.append((name, None, None))
    return tuple(out)


def load(path: Path | str = LEDGER, *, fresh: bool = False) -> Ledger:
    """Завантажує облік через beancount.loader; перечитує, лише якщо файли змінилися."""
    from beancount import loader  # імпорт тут: check.py і тести можуть підмінити

    key = str(path)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and not fresh and _stamp(cached[1].files) == cached[0]:
            return cached[1]
        entries, errors, options = loader.load_file(key)
        files = tuple(options.get("include") or (key,))
        ledger = Ledger(entries, errors, files)
        _cache[key] = (_stamp(files), ledger)
        return ledger


def is_transaction(entry) -> bool:
    return type(entry).__name__ == "Transaction"


# --------------------------------------------------------------------------
# Місячні підсумки
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """Одна витрата для розгорнутого списку категорії."""
    date: dt.date
    narration: str
    amount: Decimal
    who: str
    fixed: bool
    shared: bool
    cash: bool


@dataclass
class PersonMonth:
    income: Decimal = ZERO
    expenses: Decimal = ZERO
    fixed: Decimal = ZERO
    shared_paid: Decimal = ZERO
    categories: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    items: dict[str, list[Item]] = field(default_factory=lambda: defaultdict(list))

    @property
    def variable(self) -> Decimal:
        return self.expenses - self.fixed

    @property
    def saved(self) -> Decimal:
        return self.income - self.expenses

    @property
    def rate(self) -> Decimal | None:
        """Норма заощаджень у відсотках; None, якщо доходу в місяці не було."""
        if self.income <= 0:
            return None
        return self.saved / self.income * 100

    def merge(self, other: PersonMonth) -> PersonMonth:
        out = PersonMonth(self.income + other.income, self.expenses + other.expenses,
                          self.fixed + other.fixed, self.shared_paid + other.shared_paid)
        for src in (self.categories, other.categories):
            for k, v in src.items():
                out.categories[k] += v
        for src in (self.items, other.items):
            for k, v in src.items():
                out.items[k].extend(v)
        return out

    def items_of(self, category: str) -> list[Item]:
        """Операції категорії, найновіші зверху, при однаковій даті — більші вище."""
        return sorted(self.items.get(category, ()), key=lambda i: (i.date, i.amount), reverse=True)

    def top_categories(self) -> list[tuple[str, Decimal]]:
        return sorted(((k, v) for k, v in self.categories.items() if v), key=lambda kv: -kv[1])


@dataclass
class Debt:
    debtor: str | None      # хто винен (None — квиті)
    creditor: str | None
    amount: Decimal

    def phrase(self) -> str:
        """«Марко винен Олі» / «Оля винна Марку» — без суми."""
        if not self.debtor:
            return "Квиті"
        verb = "винна" if self.debtor == "Оля" else "винен"
        return f"{self.debtor} {verb} {DATIVE[self.creditor]}"


@dataclass
class Month:
    year: int
    month: int
    through: dt.date
    people: dict[str, PersonMonth]

    @property
    def key(self) -> str:
        return f"{self.year}-{self.month:02d}"

    @property
    def partial(self) -> bool:
        return self.through.day < calendar.monthrange(self.year, self.month)[1]

    def view(self, who: str) -> PersonMonth:
        if who == TOGETHER:
            a, b = (self.people[p] for p in PEOPLE)
            return a.merge(b)
        return self.people[who]

    @property
    def debt(self) -> Debt:
        """Спільне ділимо навпіл: кожен мав заплатити половину загальної суми."""
        paid = {p: self.people[p].shared_paid for p in PEOPLE}
        half_diff = (paid[PEOPLE[0]] - paid[PEOPLE[1]]) / 2
        half_diff = half_diff.quantize(Decimal("0.01"))
        if half_diff > 0:
            return Debt(PEOPLE[1], PEOPLE[0], half_diff)
        if half_diff < 0:
            return Debt(PEOPLE[0], PEOPLE[1], -half_diff)
        return Debt(None, None, ZERO)

    @property
    def shared_total(self) -> Decimal:
        return sum((self.people[p].shared_paid for p in PEOPLE), ZERO)


def _split(account: str) -> tuple[str, str, str | None]:
    """'Expenses:Доставка-Їжі:Марко' -> ('Expenses', 'Доставка-Їжі', 'Марко')."""
    parts = account.split(":")
    person = parts[-1] if parts[-1] in PEOPLE else None
    category = ":".join(parts[1:-1] if person else parts[1:])
    return parts[0], category, person


def months(entries) -> list[Month]:
    """Підсумки по місяцях, від найстаршого до найновішого."""
    data: dict[tuple[int, int], dict[str, PersonMonth]] = {}
    through: dict[tuple[int, int], dt.date] = {}

    for entry in entries:
        if not is_transaction(entry):
            continue
        tags = entry.tags or frozenset()
        for posting in entry.postings:
            root, category, person = _split(posting.account)
            if root not in ("Income", "Expenses") or person is None:
                continue
            ym = (entry.date.year, entry.date.month)
            bucket = data.setdefault(ym, {p: PersonMonth() for p in PEOPLE})[person]
            through[ym] = max(through.get(ym, entry.date), entry.date)
            number = posting.units.number
            if root == "Income":
                bucket.income -= number
            else:
                bucket.expenses += number
                bucket.categories[category] += number
                bucket.items[category].append(Item(
                    entry.date, entry.narration or entry.payee or "", number, person,
                    TAG_FIXED in tags, TAG_SHARED in tags, TAG_CASH in tags))
                if TAG_FIXED in tags:
                    bucket.fixed += number
                if TAG_SHARED in tags:
                    bucket.shared_paid += number

    return [Month(y, m, through[(y, m)], data[(y, m)]) for (y, m) in sorted(data)]


@dataclass(frozen=True)
class ManualEntry:
    date: dt.date
    narration: str
    who: str
    category: str
    amount: Decimal
    shared: bool


def manual_entries(entries, limit: int = 15) -> list[ManualEntry]:
    """Останні ручні витрати (з manual.beancount), найновіші зверху."""
    manual = str(MANUAL.resolve())
    found = []
    for e in entries:
        if not is_transaction(e) or str(Path(e.meta.get("filename", "")).resolve()) != manual:
            continue
        for posting in e.postings:
            root, category, person = _split(posting.account)
            if root == "Expenses" and person:
                found.append((e.meta.get("lineno", 0), ManualEntry(
                    e.date, e.narration, person, category, posting.units.number,
                    TAG_SHARED in (e.tags or ()))))
    found.sort(key=lambda x: (x[1].date, x[0]), reverse=True)
    return [m for _, m in found[:limit]]
