"""Імпорт банківських виписок Олі й Марка в Beancount.

Кожен банк має свій формат CSV, тому парсерів два, а далі все спільне:
правила з rules.csv, теги #fixed і #shared, звірка залишку з банком.

  data/olya-*.csv   -> transactions-olya.beancount
  data/marko-*.csv  -> transactions-marko.beancount

Обидва файли перезаписуються повністю, руками їх не правимо.
Ручні операції (готівка з телефона) живуть окремо в manual.beancount,
імпорт їх не чіпає.

Запуск: uv run python import.py (тонка обгортка над цим модулем)
"""

from __future__ import annotations

import csv
import datetime as dt
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RULES_FILE = ROOT / "rules.csv"

CURRENCY = "PLN"
OPENING = "Equity:Початковий-Залишок"
UNCATEGORIZED = "Expenses:Без-Категорії"
OTHER_INCOME = "Income:Інше"
BANK_FEES = "Expenses:Комісії-Банку"

TAG_FIXED = "fixed"
TAG_SHARED = "shared"


class StatementError(Exception):
    """Виписка не відповідає очікуванням (формат, залишок, валюта)."""


@dataclass(frozen=True)
class Row:
    date: dt.date
    description: str
    amount: Decimal       # сума операції з боку рахунку: мінус — гроші пішли
    fee: Decimal          # комісія банку, додатна
    balance: Decimal      # залишок після операції за даними банку


@dataclass(frozen=True)
class Rule:
    pattern: str
    account: str
    fixed: bool
    shared: bool


# --------------------------------------------------------------------------
# Формати банків
# --------------------------------------------------------------------------

def parse_olya(path: Path) -> tuple[Decimal | None, list[Row]]:
    """Банк Олі: CSV з комою, крапка в сумах, рядки в порядку банку."""
    rows: list[Row] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["State"] != "COMPLETED":
                continue
            if r["Product"] != "Current" or r["Currency"] != CURRENCY:
                print(f"  пропускаю {path.name}: {r['Description']} ({r['Product']}, {r['Currency']})")
                continue
            rows.append(Row(
                date=dt.date.fromisoformat(r["Completed Date"][:10]),
                description=r["Description"].strip(),
                amount=Decimal(r["Amount"]),
                fee=Decimal(r["Fee"] or "0"),
                balance=Decimal(r["Balance"]),
            ))
    return None, rows  # початковий залишок виводимо з першого рядка


def _pl_number(text: str) -> Decimal:
    """'11 212,47' / '-92,12' / '7600,00' -> Decimal."""
    cleaned = text.replace(" ", "").replace(" ", "").replace(",", ".")
    return Decimal(cleaned)


def _pl_date(text: str) -> dt.date:
    day, month, year = text.strip().split(".")
    return dt.date(int(year), int(month), int(day))


def parse_marko(path: Path) -> tuple[Decimal | None, list[Row]]:
    """Банк Марка: шапка з метаданими, далі таблиця через ';', кома в сумах,
    пробіли в тисячах, найновіші операції зверху.

    Дата операції — «Data operacji» (коли Марко платив), а не дата проведення.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    meta: dict[str, str] = {}
    header_at = None
    for i, line in enumerate(lines):
        if line.startswith("Data operacji;"):
            header_at = i
            break
        if ";" in line:
            key, _, value = line.partition(";")
            meta[key.strip()] = value.strip()
    if header_at is None:
        raise StatementError(f"{path.name}: не знайшов рядок заголовка 'Data operacji;…'")
    if meta.get("Waluta", CURRENCY) != CURRENCY:
        raise StatementError(f"{path.name}: валюта {meta['Waluta']}, очікував {CURRENCY}")

    reader = csv.DictReader(lines[header_at:], delimiter=";")
    rows = [
        Row(
            date=_pl_date(r["Data operacji"]),
            description=" ".join(r["Opis"].split()),
            amount=_pl_number(r["Kwota"]),
            fee=Decimal("0"),
            balance=_pl_number(r["Saldo po operacji"]),
        )
        for r in reader
        if r.get("Data operacji", "").strip()
    ]
    rows.reverse()  # тепер від найстаршої до найновішої, як рахується залишок
    opening = _pl_number(meta["Saldo początkowe"]) if "Saldo początkowe" in meta else None
    return opening, rows


@dataclass(frozen=True)
class Bank:
    person: str            # як у назвах рахунків: Оля, Марко
    slug: str              # латиницею, для імен файлів
    parse: Callable[[Path], tuple[Decimal | None, list[Row]]]

    @property
    def account(self) -> str:
        return f"Assets:Банк:{self.person}"

    @property
    def out(self) -> Path:
        return ROOT / f"transactions-{self.slug}.beancount"

    def files(self) -> list[Path]:
        return sorted(DATA.glob(f"{self.slug}-*.csv"))


BANKS = [
    Bank("Оля", "olya", parse_olya),
    Bank("Марко", "marko", parse_marko),
]


# --------------------------------------------------------------------------
# Категорії
# --------------------------------------------------------------------------

def load_rules() -> list[Rule]:
    def yes(value: str) -> bool:
        return value.strip().lower() == "так"

    with open(RULES_FILE, encoding="utf-8-sig", newline="") as f:
        return [
            Rule(r["шаблон"].strip().lower(), r["рахунок"].strip(),
                 yes(r["фіксована"]), yes(r["спільна"]))
            for r in csv.DictReader(f)
            if r["шаблон"].strip()
        ]


def categorize(description: str, amount: Decimal, rules: list[Rule]) -> Rule:
    """Перше правило, чий шаблон є в описі (без урахування регістру).

    Надходження за правилом витрат (повернення від магазину) і будь-яке
    надходження без правила — Income:Інше.
    """
    text = description.lower()
    for rule in rules:
        if rule.pattern in text:
            if amount > 0 and rule.account.startswith("Expenses:"):
                break
            return rule
    account = OTHER_INCOME if amount > 0 else UNCATEGORIZED
    return Rule("", account, False, False)


# --------------------------------------------------------------------------
# Запис у Beancount
# --------------------------------------------------------------------------

def quote(text: str) -> str:
    return '"' + text.replace("\\", "/").replace('"', "'") + '"'


def money(number: Decimal) -> str:
    return f"{number:.2f} {CURRENCY}"


def collect(bank: Bank) -> tuple[Decimal, list[Row]]:
    files = bank.files()
    if not files:
        raise StatementError(f"У data/ немає виписок {bank.slug}-*.csv")
    opening: Decimal | None = None
    rows: list[Row] = []
    seen: set[tuple] = set()
    for path in files:
        file_opening, file_rows = bank.parse(path)
        if opening is None:
            opening = file_opening
        for row in file_rows:
            key = (row.date, row.description, row.amount, row.balance)
            if key in seen:  # перекриття сусідніх виписок
                continue
            seen.add(key)
            rows.append(row)
    if not rows:
        raise StatementError(f"{bank.slug}: у виписках немає жодної завершеної операції")

    first = rows[0]
    derived = first.balance - (first.amount - first.fee)
    if opening is None:
        opening = derived
    elif opening != derived:
        raise StatementError(
            f"{bank.slug}: початковий залишок {opening} не сходиться з першою операцією ({derived})")

    # Залишок має сходитися рядок за рядком, інакше парсер щось загубив.
    running = opening
    for row in rows:
        running += row.amount - row.fee
        if running != row.balance:
            raise StatementError(
                f"{bank.slug}: {row.date} «{row.description}» — розрахунковий залишок "
                f"{running}, у банку {row.balance}")
    return opening, rows


def render(bank: Bank, opening: Decimal, rows: list[Row], rules: list[Rule]) -> tuple[str, list[str]]:
    person = bank.person
    lines = [
        f"; Згенеровано import.py з data/{bank.slug}-*.csv і rules.csv. Руками не редагувати.",
        "",
        f'{rows[0].date - dt.timedelta(days=1)} * "Початковий залишок"',
        f"  {bank.account}  {money(opening)}",
        f"  {OPENING}",
        "",
    ]
    uncategorized: list[str] = []
    month_end: dict[tuple[int, int], tuple[dt.date, Decimal]] = {}

    for row in rows:
        rule = categorize(row.description, row.amount, rules)
        if rule.account == UNCATEGORIZED:
            uncategorized.append(row.description)
        tags = "".join(f" #{t}" for t, on in ((TAG_FIXED, rule.fixed), (TAG_SHARED, rule.shared)) if on)
        lines.append(f"{row.date} * {quote(row.description)}{tags}")
        lines.append(f"  {bank.account}  {money(row.amount - row.fee)}")
        lines.append(f"  {rule.account}:{person}  {money(-row.amount)}")
        if row.fee:
            lines.append(f"  {BANK_FEES}:{person}  {money(row.fee)}")
        lines.append("")
        month_end[(row.date.year, row.date.month)] = (row.date, row.balance)

    # Звірка з банком: залишок на 1-ше число наступного місяця,
    # для останнього (неповного) місяця — на день після останньої операції.
    months = sorted(month_end)
    for i, (year, month) in enumerate(months):
        last_date, balance = month_end[(year, month)]
        when = (dt.date(year + month // 12, month % 12 + 1, 1)
                if i < len(months) - 1 else last_date + dt.timedelta(days=1))
        lines.append(f"{when} balance {bank.account}  {money(balance)}")

    return "\n".join(lines) + "\n", uncategorized


def run(verbose: bool = True) -> list[str]:
    """Імпортує всі банки. Повертає описи операцій без категорії."""
    rules = load_rules()
    all_uncategorized: list[str] = []
    for bank in BANKS:
        opening, rows = collect(bank)
        text, uncategorized = render(bank, opening, rows, rules)
        bank.out.write_text(text, encoding="utf-8")
        all_uncategorized += uncategorized
        if verbose:
            print(f"{bank.person}: {len(rows)} операцій, залишок звірено рядок за рядком "
                  f"→ {bank.out.name}")
            if uncategorized:
                print(f"  без категорії {len(uncategorized)}: " + ", ".join(sorted(set(uncategorized))))
    return all_uncategorized


if __name__ == "__main__":
    try:
        run()
    except StatementError as e:
        sys.exit(f"Помилка імпорту: {e}")
