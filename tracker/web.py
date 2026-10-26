"""Вебзастосунок трекера: вхід за паролем, звіт на телефоні, готівка, Fava.

  /         звіт за місяць (Марко / Оля / Разом)
  /add      додати готівкову витрату
  /fava/    повний Fava за тим самим входом (детальні таблиці, кнопка +)
  /login, /logout

Жодна сторінка, крім /login, не віддає даних без входу. Fava теж:
запити до /fava без сесії отримують редірект на вхід (або 401 для API).

Локально: uv run python -m tracker.web   → http://127.0.0.1:5050
На сервері: WSGI-змінна application з wsgi.py.
"""

from __future__ import annotations

import datetime as dt
import hmac
import inspect
import logging
import secrets
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import (Flask, Response, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

from . import finance, manual
from .auth import DEFAULT_VIEW, RateLimiter, Settings, password_version, verify

log = logging.getLogger("tracker")

TZ = ZoneInfo("Europe/Warsaw")
VIEWS = (*finance.PEOPLE, finance.TOGETHER)
MONTHS = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
          "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
PUBLIC_ENDPOINTS = {"login"}

SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
    ("X-Robots-Tag", "noindex, nofollow"),
]
HSTS = ("Strict-Transport-Security", "max-age=31536000")
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "script-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


# --------------------------------------------------------------------------
# Форматування
# --------------------------------------------------------------------------

def zl(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    text = f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} зл"


def pct(value: Decimal | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{Decimal(value):.{digits}f}".replace(".", ",") + "%"


def label(category: str) -> str:
    return {"Звязок": "Зв'язок"}.get(category, category).replace("-", " ")


def ops(n: int) -> str:
    """1 операція, 2 операції, 5 операцій."""
    if n % 10 == 1 and n % 100 != 11:
        word = "операція"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = "операції"
    else:
        word = "операцій"
    return f"{n} {word}"


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def safe_next(target: str | None) -> str:
    """Лише відносний шлях у межах сайту — без відкритого редіректу."""
    if not target:
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


# --------------------------------------------------------------------------
# Fava
# --------------------------------------------------------------------------

def make_fava(ledger_path: Path):
    """Fava з тим самим обліком. Параметри передаємо лише ті, що є у версії."""
    from fava.application import create_app as fava_create_app

    wanted = {"load": True, "poll_watcher": True, "read_only": False, "incognito": False}
    accepted = inspect.signature(fava_create_app).parameters
    return fava_create_app([str(ledger_path)], **{k: v for k, v in wanted.items() if k in accepted})


# --------------------------------------------------------------------------
# Застосунок
# --------------------------------------------------------------------------

def create_app(settings: Settings | None = None, ledger_path: Path = finance.LEDGER,
               fava_app=None):
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        SESSION_COOKIE_NAME="tracker_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=not settings.dev,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    app.jinja_env.filters.update(zl=zl, pct=pct, label=label, ops=ops)
    app.jinja_env.globals.update(VIEWS=VIEWS, MONTHS=MONTHS)

    ip_limiter = RateLimiter(limit=5, window_s=15 * 60)
    login_limiter = RateLimiter(limit=10, window_s=60 * 60)

    # ---------------- сесія і CSRF ----------------

    def current_user() -> str | None:
        login = session.get("user")
        if login and login in settings.users and \
                hmac.compare_digest(session.get("pwv", ""), password_version(settings, login)):
            return login
        return None

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def guard():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                return Response("Сторінка застаріла. Поверніться назад і оновіть її.", 400,
                                mimetype="text/plain")
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if current_user() is None:
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return None

    @app.after_request
    def headers(resp: Response):
        for k, v in SECURITY_HEADERS:
            resp.headers.setdefault(k, v)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["Cache-Control"] = "no-store"
        if not settings.dev:
            resp.headers.setdefault(*HSTS)
        return resp

    @app.context_processor
    def inject_user():
        return {"user": current_user()}

    # ---------------- вхід ----------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if current_user():
                return redirect(safe_next(request.args.get("next")))
            return render_template("login.html", next=safe_next(request.args.get("next")))

        login_name = (request.form.get("login") or "").strip().lower()
        password = request.form.get("password") or ""
        nxt = safe_next(request.form.get("next"))
        ip = request.remote_addr or "?"
        if ip_limiter.blocked(ip) or login_limiter.blocked(login_name):
            log.warning("login blocked ip=%s login=%s", ip, login_name)
            return render_template("login.html", next=nxt, login=login_name,
                                   error="Забагато спроб. Спробуйте за 15 хвилин."), 429
        if not verify(settings, login_name, password):
            ip_limiter.fail(ip)
            login_limiter.fail(login_name)
            log.warning("login failed ip=%s login=%s", ip, login_name)
            return render_template("login.html", next=nxt, login=login_name,
                                   error="Неправильний логін або пароль."), 401

        ip_limiter.reset(ip)
        login_limiter.reset(login_name)
        session.clear()  # нова сесія після входу
        session.permanent = True
        session["user"] = login_name
        session["pwv"] = password_version(settings, login_name)
        log.info("login ok ip=%s login=%s", ip, login_name)
        return redirect(nxt)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------------- звіт ----------------

    @app.get("/")
    def report():
        ledger = finance.load(ledger_path)
        months = finance.months(ledger.entries)
        if not months:
            return render_template("report.html", month=None, months=[], errors=ledger.errors)
        by_key = {m.key: m for m in months}
        month = by_key.get(request.args.get("m", ""), months[-1])
        who = request.args.get("who")
        if who not in VIEWS:
            who = DEFAULT_VIEW.get(current_user() or "", finance.TOGETHER)
        view = month.view(who)
        cats = view.top_categories()
        return render_template(
            "report.html", months=months, month=month, who=who, view=view,
            cats=cats, cat_max=max((v for _, v in cats), default=Decimal(1)),
            debt=month.debt, errors=ledger.errors,
            through=month.through.strftime("%d.%m"),
        )

    # ---------------- готівка ----------------

    @app.route("/add", methods=["GET", "POST"])
    def add():
        user = current_user()
        defaults = {
            "who": DEFAULT_VIEW.get(user, finance.PEOPLE[1]),
            "date": today().isoformat(),
            "category": "Продукти",
            "amount": "", "note": "", "shared": "",
        }
        error = None
        if request.method == "POST":
            try:
                expense = manual.parse_form(request.form, user, today())
                manual.append(expense, ledger_path, ledger_path.parent / "manual.beancount")
            except manual.EntryError as e:
                error = str(e)
                defaults.update({k: request.form.get(k, "") for k in defaults})
            else:
                log.info("cash added by=%s who=%s amount=%s", user, expense.who, expense.amount)
                flash(f"Збережено: {zl(expense.amount)}, {label(expense.category).lower()}, "
                      f"{expense.date.strftime('%d.%m')}.")
                return redirect(url_for("report", m=f"{expense.date:%Y-%m}", who=expense.who))

        ledger = finance.load(ledger_path)
        recent = finance.manual_entries(ledger.entries)
        return render_template("add.html", form=defaults, error=error, recent=recent,
                               categories=finance.CASH_CATEGORIES), (400 if error else 200)

    # ---------------- збірка WSGI ----------------

    fava = fava_app if fava_app is not None else make_fava(ledger_path)
    mounted = DispatcherMiddleware(Response("Not found", 404), {"/fava": fava})

    def add_headers(start_response):
        def _start(status, response_headers, exc_info=None):
            names = {k.lower() for k, _ in response_headers}
            extra = [h for h in SECURITY_HEADERS if h[0].lower() not in names]
            if not settings.dev:
                extra.append(HSTS)
            return start_response(status, list(response_headers) + extra, exc_info)
        return _start

    def core(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/fava" or path.startswith("/fava/"):
            with app.request_context(environ):
                if current_user() is None:
                    if "/api/" in path:
                        resp = Response('{"error": "login required"}', 401,
                                        mimetype="application/json")
                    else:
                        target = path + ("?" + environ["QUERY_STRING"] if environ.get("QUERY_STRING") else "")
                        resp = redirect(url_for("login", next=target))
                    return resp(environ, add_headers(start_response))
            return mounted(environ, add_headers(start_response))
        return app.wsgi_app(environ, start_response)

    def https_only(environ, start_response):
        # PythonAnywhere (і більшість хостингів) ставить X-Forwarded-Proto.
        # Без заголовка не редіректимо, щоб не зациклитися; там HTTPS вмикає «Force HTTPS».
        if not settings.dev and environ.get("HTTP_X_FORWARDED_PROTO", "").lower() == "http":
            host = environ.get("HTTP_HOST", "")
            target = "https://" + host + environ.get("PATH_INFO", "/")
            if environ.get("QUERY_STRING"):
                target += "?" + environ["QUERY_STRING"]
            return redirect(target, 301)(environ, start_response)
        return core(environ, start_response)

    application = ProxyFix(https_only, x_for=1, x_proto=1, x_host=0)
    application.flask_app = app  # для тестів
    return application


def main() -> None:  # pragma: no cover - локальний запуск
    import os

    from werkzeug.serving import run_simple

    os.environ.setdefault("TRACKER_DEV", "1")
    logging.basicConfig(level=logging.INFO)
    run_simple("127.0.0.1", 5050, create_app(), use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
