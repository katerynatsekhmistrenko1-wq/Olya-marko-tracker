"""Одна команда перевірки: імпорт обох виписок, bean-check, контрольні цифри.

  uv run python check.py            # з імпортом (звичайний запуск)
  uv run python check.py --no-import

Код виходу 0 — усе зійшлося; 1 — bean-check знайшов помилки або цифри не ті.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from tracker import finance, importer

# Змінні витрати з умови завдання (усе, крім тегу #fixed).
EXPECTED_VARIABLE = {
    ("Оля", "2026-08"): Decimal("2195.21"),
    ("Оля", "2026-09"): Decimal("2582.42"),
    ("Марко", "2026-08"): Decimal("1855.57"),
    ("Марко", "2026-09"): Decimal("2432.83"),
}
MONTH_NAMES = {"2026-08": "серпень", "2026-09": "вересень"}

OK, FAIL = "✓", "✗"


def fmt(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def bean_check(ledger: Path) -> bool:
    """Запускає справжній bean-check з того ж середовища, що й цей скрипт."""
    exe = shutil.which("bean-check", path=str(Path(sys.executable).parent)) or shutil.which("bean-check")
    if exe:
        result = subprocess.run([exe, str(ledger)], capture_output=True, text=True)
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0 and not output:
            print(f"bean-check {ledger.name}: помилок немає {OK}")
            return True
        print(f"bean-check {ledger.name}: {FAIL}\n{output}")
        return False

    # Запасний шлях: той самий завантажувач, що використовує bean-check.
    errors = finance.load(ledger, fresh=True).errors
    if not errors:
        print(f"bean-check (через beancount.loader) {ledger.name}: помилок немає {OK}")
        return True
    from beancount.parser import printer

    print(f"bean-check {ledger.name}: {len(errors)} помилок {FAIL}")
    printer.print_errors(errors, file=sys.stdout)
    return False


def main(argv: list[str]) -> int:
    ok = True
    if "--no-import" not in argv:
        print("1. Імпорт виписок")
        try:
            uncategorized = importer.run()
        except importer.StatementError as e:
            print(f"Помилка імпорту: {e} {FAIL}")
            return 1
        if uncategorized:
            print(f"   Без категорії: {len(uncategorized)} — додайте правила в rules.csv {FAIL}")
            ok = False
        print()

    print("2. Перевірка обліку")
    ok &= bean_check(finance.LEDGER)
    print()

    print("3. Контрольні цифри: змінні витрати (без #fixed)")
    ledger = finance.load(finance.LEDGER, fresh=True)
    months = {m.key: m for m in finance.months(ledger.entries)}
    header = f"   {'':<8}" + "".join(f"{MONTH_NAMES[k]:>18}" for k in MONTH_NAMES)
    print(header)
    for person in finance.PEOPLE:
        cells = []
        for key in MONTH_NAMES:
            got = months[key].people[person].variable if key in months else Decimal("0")
            want = EXPECTED_VARIABLE[(person, key)]
            mark = OK if got == want else f"{FAIL} (треба {fmt(want)})"
            ok &= got == want
            cells.append(f"{fmt(got):>14} {mark}")
        print(f"   {person:<8}" + "".join(f"{c:>18}" for c in cells))
    print()

    print("4. Спільні витрати (оренда і супермаркети навпіл)")
    for key in MONTH_NAMES:
        if key in months:
            d = months[key].debt
            text = f"{d.phrase()} {fmt(d.amount)} зл" if d.debtor else "квиті"
            print(f"   {MONTH_NAMES[key]:<9} {text}")
    print()

    print("Усе зійшлося." if ok else "Є розбіжності, див. вище.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
