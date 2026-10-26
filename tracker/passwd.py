"""Секрети для трекера: хеші паролів, ключ сесії, готовий .env.

  python -m tracker.passwd --write-env --dev   # локально: спитає паролі marko і olya, запише .env
  python -m tracker.passwd --write-env         # на сервері: те саме, але без TRACKER_DEV
  python -m tracker.passwd marko               # лише надрукувати marko:<хеш>
  python -m tracker.passwd --secret            # лише надрукувати випадковий TRACKER_SECRET_KEY

Паролі вводяться приховано і ніде не зберігаються, лише хеші. Існуючий .env
не перезаписується без --force.
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
LOGINS = ("marko", "olya")
MIN_LEN = 10


def ask_password(login: str) -> str:
    while True:
        password = getpass.getpass(f"Пароль для {login} (мін. {MIN_LEN} символів): ")
        if len(password) < MIN_LEN:
            print(f"Закороткий, потрібно щонайменше {MIN_LEN} символів.", file=sys.stderr)
            continue
        if getpass.getpass("Ще раз: ") != password:
            print("Паролі не збігаються, спробуйте ще.", file=sys.stderr)
            continue
        return password


def write_env(dev: bool, force: bool) -> int:
    path = ROOT / ".env"
    if path.exists() and path.stat().st_size > 0 and not force:
        print(f"{path} уже є. Щоб перезаписати: додайте --force.", file=sys.stderr)
        return 1
    users = ";".join(f"{login}:{generate_password_hash(ask_password(login))}" for login in LOGINS)
    lines = [
        f"TRACKER_SECRET_KEY={secrets.token_urlsafe(48)}",
        f"TRACKER_USERS={users}",
    ]
    if dev:
        lines.append("TRACKER_DEV=1")
    # ASCII/UTF-8 без BOM, \n — читається однаково на Windows, macOS і сервері.
    path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    mode = "локальний (http дозволено)" if dev else "серверний (лише HTTPS)"
    print(f"Записано {path}: користувачі {', '.join(LOGINS)}, режим {mode}.")
    return 0


def main(argv: list[str]) -> int:
    if "--write-env" in argv:
        return write_env(dev="--dev" in argv, force="--force" in argv)
    if argv[:1] == ["--secret"]:
        print(secrets.token_urlsafe(48))
        return 0
    if len(argv) != 1 or not argv[0].isidentifier():
        print(__doc__)
        return 2
    login = argv[0].lower()
    print(f"{login}:{generate_password_hash(ask_password(login))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
