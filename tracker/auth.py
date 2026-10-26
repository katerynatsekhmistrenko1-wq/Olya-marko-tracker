"""Налаштування з середовища, користувачі, обмеження спроб входу.

Секрети тільки в змінних середовища (або у файлі .env поруч, який не
потрапляє в git). У коді й репозиторії паролів немає — лише хеші, і ті на сервері.

  TRACKER_SECRET_KEY   випадковий рядок для підпису cookie сесії (>= 32 символи)
  TRACKER_USERS        логін:хеш;логін:хеш   (хеш дає python -m tracker.passwd)
  TRACKER_DEV=1        лише локально: дозволяє http і несекьюрні cookie
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from . import finance

# Логін -> чий звіт відкривати за замовчуванням.
DEFAULT_VIEW = {"marko": "Марко", "olya": "Оля"}


class ConfigError(RuntimeError):
    pass


def load_dotenv(path: Path) -> None:
    """Мінімальний .env: KEY=VALUE на рядок, # — коментар. Не перезаписує вже задане."""
    if not path.exists():
        return
    data = path.read_bytes()
    # Блокнот у Windows може зберегти файл з BOM або в UTF-16 («Unicode»).
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    secret_key: str
    users: dict[str, str]      # логін -> хеш пароля
    dev: bool

    @classmethod
    def from_env(cls) -> Settings:
        env_file = finance.ROOT / ".env"
        load_dotenv(env_file)
        secret = os.environ.get("TRACKER_SECRET_KEY", "")
        if len(secret) < 32:
            if env_file.exists():
                hint = f"Файл {env_file} знайдено, але в ньому немає рядка TRACKER_SECRET_KEY=… (32+ символи)."
            elif env_file.with_name(".env.txt").exists():
                hint = f"Знайдено {env_file.name}.txt — перейменуйте його на .env (без .txt)."
            else:
                hint = f"Файлу {env_file} немає."
            raise ConfigError("TRACKER_SECRET_KEY не задано або коротший за 32 символи. " + hint +
                              " Згенеруйте: python -m tracker.passwd --secret")
        users: dict[str, str] = {}
        for item in os.environ.get("TRACKER_USERS", "").split(";"):
            login, sep, pw_hash = item.strip().partition(":")
            if sep and login and pw_hash:
                users[login.strip().lower()] = pw_hash.strip()
        if not users:
            raise ConfigError("TRACKER_USERS порожній. Формат: логін:хеш;логін:хеш "
                              "(хеш дає python -m tracker.passwd ЛОГІН)")
        return cls(secret, users, os.environ.get("TRACKER_DEV") == "1")


def verify(settings: Settings, login: str, password: str) -> bool:
    pw_hash = settings.users.get(login)
    if pw_hash is None:
        # Порівнюємо з фіктивним хешем, щоб час відповіді не видавав, чи існує логін.
        check_password_hash(_DUMMY_HASH, password)
        return False
    return check_password_hash(pw_hash, password)


def password_version(settings: Settings, login: str) -> str:
    """Шматок хешу в сесії: змінили пароль — старі сесії перестають діяти."""
    return settings.users.get(login, "")[-16:]


# Хеш випадкового пароля, згенерований при старті: потрібен лише для вирівнювання часу.
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(16))


class RateLimiter:
    """Не більше N невдалих спроб за вікно часу на ключ (IP або логін)."""

    def __init__(self, limit: int, window_s: int):
        self.limit, self.window = limit, window_s
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, key: str) -> bool:
        with self._lock:
            q = self._fails[key]
            self._trim(q, time.monotonic())
            return len(q) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            q = self._fails[key]
            now = time.monotonic()
            self._trim(q, now)
            q.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
