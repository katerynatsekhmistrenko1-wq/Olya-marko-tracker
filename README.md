# Трекер Олі й Марка

Спільний фінансовий облік на Beancount 3 + Fava, який Марко відкриває з телефона.
Домашнє завдання 3, частина 2 (Genesis Warsaw IT School 5.0).

> **Усі дані в репозиторії штучні** — це виписки з умови завдання. Справжніх виписок сюди не кладіть.

## Що вміє

- Імпортує обидві виписки (у банків різні формати) в один журнал і звіряє залишок з банком рядок за рядком.
- Звіт за місяць для Марка, Олі або разом: витрати за категоріями (кожна розгортається в список операцій), норма заощаджень, фіксовані й змінні.
- Спільні витрати (оренда й супермаркети навпіл): хто кому скільки винен.
- «+ Додати готівку» з телефона — запис одразу потрапляє в облік і звіт.
- Повна Fava під `/fava/` за тим самим входом.

---

## 1. Перевірка однією командою

```bash
uv sync
uv run python check.py
```

Команда імпортує `data/*.csv`, запускає `bean-check main.beancount` і друкує контрольні цифри. Очікуваний результат:

```
2. Перевірка обліку
bean-check main.beancount: помилок немає ✓

3. Контрольні цифри: змінні витрати (без #fixed)
                      серпень          вересень
   Оля             2 195,21 ✓        2 582,42 ✓
   Марко           1 855,57 ✓        2 432,83 ✓

4. Спільні витрати (оренда і супермаркети навпіл)
   серпень   Марко винен Олі 1 713,81 зл
   вересень  Марко винен Олі 1 566,47 зл

Усе зійшлося.
```

Код виходу `0` — усе збіглося, `1` — помилка в bean-check або цифри не ті.

Без uv: `pip install -r requirements.txt`, потім `python check.py` (потрібен Python 3.12+).

---

## 2. Локальний запуск

### 2.1. Встановити uv

uv сам завантажить Python 3.12, окремо Python ставити не треба.

| Система | Команда |
|---|---|
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Після встановлення **закрийте і відкрийте термінал заново**, перевірте: `uv --version`.

### 2.2. Перейти в папку проєкту і встановити залежності

```bash
cd шлях/до/olya-marko-tracker     # там, де лежать check.py і README.md
uv sync
uv run python check.py            # має закінчитися «Усе зійшлося.»
```

> Windows часто розпаковує zip у дві вкладені папки з однаковою назвою. Правильна та, де `dir` (або `ls`) показує `check.py`.

### 2.3. Створити `.env`

`.env` зберігає секрети; він у `.gitignore` і в репозиторій не потрапляє. Одна команда спитає паролі для `marko` і `olya` (приховано, двічі, мінімум 10 символів) і запише правильний файл:

```bash
uv run python -m tracker.passwd --write-env --dev
```

`--dev` додає `TRACKER_DEV=1` — **лише локально**, дозволяє http на 127.0.0.1. Наявний `.env` команда не перезаписує; щоб змінити паролі, додайте `--force`.

<details>
<summary>Вручну, якщо потрібно</summary>

```bash
uv run python -m tracker.passwd --secret     # → рядок для TRACKER_SECRET_KEY
uv run python -m tracker.passwd marko        # → marko:scrypt:...
uv run python -m tracker.passwd olya         # → olya:scrypt:...
```

`.env` — три рядки, без пробілів довкола `=` і без лапок; `TRACKER_USERS` в один рядок, користувачі через `;`:

```
TRACKER_SECRET_KEY=...
TRACKER_USERS=marko:scrypt:...;olya:scrypt:...
TRACKER_DEV=1
```

У Windows не користуйтеся Блокнотом (зберігає `.env.txt` або порожній файл). У PowerShell — значення в **одинарних** лапках, бо в подвійних зіпсуються `$` у хешах:

```powershell
Set-Content -Path .env -Encoding ascii -Value @('TRACKER_SECRET_KEY=...', 'TRACKER_USERS=...', 'TRACKER_DEV=1')
```
</details>

### 2.4. Запустити

```bash
uv run python -m tracker.web
```

Відкрийте http://127.0.0.1:5050, увійдіть як `marko`. Сервер слухає лише ваш комп'ютер; вигляд на телефоні — через DevTools (F12 → режим пристрою).

Що перевірити:
1. Без входу `/`, `/add`, `/fava/` перекидають на сторінку входу.
2. Звіт → «Серпень», Марко: змінні 1 855,57 зл. Тап по категорії відкриває операції.
3. «+ Додати готівку» → 1 зл → «Зберегти» → перезавантажити: запис є у звіті.
4. «Детально» відкриває Fava.

Тестові записи потрапляють у `manual.beancount`. Перед комітом поверніть його: `git checkout manual.beancount`.

---

## 3. Публікація на GitHub

Якщо папку отримано архівом, це ще не git-репозиторій — спершу `git init -b main`. Тестові записи з `manual.beancount` приберіть, якщо додавали готівку локально (у свіжому архіві він порожній; у репозиторії — `git checkout manual.beancount`).

```bash
git init -b main                # лише якщо папка ще не репозиторій
git add -A
git status                      # .env, .venv, transactions-*.beancount НЕ мають бути в списку
git commit -m "Трекер Олі й Марка"
git remote add origin https://github.com/<you>/olya-marko-tracker.git
git push -u origin main
```

Репозиторій можна робити публічним: у ньому лише штучні дані, без паролів і ключів (перевірте: `git grep -n "scrypt:"` нічого не знаходить).

---

## 4. Деплой на PythonAnywhere (безкоштовно, HTTPS)

1. Зареєструйтеся на [pythonanywhere.com](https://www.pythonanywhere.com) (безкоштовний тариф). Адреса сайту: `https://USERNAME.pythonanywhere.com`.
2. **Consoles → Bash**:
   ```bash
   git clone https://github.com/<you>/olya-marko-tracker.git
   cd olya-marko-tracker
   mkvirtualenv --python=python3.12 tracker
   pip install -r requirements.txt
   python check.py                 # «Усе зійшлося.»
   ```
3. Створіть `.env` на сервері з **окремими** паролями (не тими, що локально; пароль `marko` потім дасте перевіряльникам):
   ```bash
   python -m tracker.passwd --write-env
   ```
   Без `--dev`: на сервері дозволено лише HTTPS.
4. **Web → Add a new web app → Manual configuration → Python 3.12**, далі на сторінці застосунку:
   - **Virtualenv:** `/home/USERNAME/.virtualenvs/tracker`
   - **WSGI configuration file:** замініть вміст на `deploy/pythonanywhere_wsgi.py`, підставивши свій `USERNAME`.
   - **Security → Force HTTPS:** увімкнути.
   - **Reload.**
5. Відкрийте `https://USERNAME.pythonanywhere.com` з телефона, увійдіть як `marko`.

Якщо сторінка показує помилку — **Web → Log files → error log**. Найчастіше: не той шлях у WSGI-файлі, не вказаний virtualenv, порожній `.env`.

Оновлення після змін у репозиторії: у Bash-консолі `cd olya-marko-tracker && git pull && workon tracker && python check.py`, потім Reload у вкладці Web.

Безкоштовний застосунок треба періодично продовжувати кнопкою у вкладці Web, інакше його вимкнуть. Трекер має працювати до 9 листопада.

---

## 5. Як влаштовано

| Файл | Що робить |
|---|---|
| `data/olya-statement.csv`, `data/marko-statement.csv` | штучні виписки з завдання |
| `rules.csv` | правила категорій: шаблон, рахунок, фіксована, спільна |
| `import.py` → `tracker/importer.py` | два парсери (у Марка `;`, кома в сумах, пробіли в тисячах, нові зверху) → `transactions-*.beancount` |
| `main.beancount` | рахунки, збережені запити для Fava, include |
| `manual.beancount` | ручні записи з телефона; імпорт його не чіпає |
| `tracker/finance.py` | усі підрахунки — і для звіту, і для `check.py`, тому цифри не розходяться |
| `tracker/web.py` | вхід, звіт, форма готівки, Fava під `/fava/` |
| `tracker/auth.py` | налаштування з `.env`, перевірка паролів, ліміт спроб |
| `check.py` | одна команда перевірки |

Особа — завжди останній рівень рахунку: `Expenses:Продукти:Марко`, `Income:Зарплата:Оля`. Теги: `#fixed` — фіксована, `#shared` — спільна, `#cash` — додано вручну.

Фіксовані: оренда, ZTM, Netflix, Spotify, Claude.ai, Play (Оля); Orange, PGE, Zdrofit, YouTube Premium (Марко). Спільні: оренда і Kaufland, Auchan, Biedronka, Lidl, Carrefour. Żabka, інтернет і світло — особисті.

Нова виписка: покласти CSV у `data/` (`olya-*.csv` або `marko-*.csv`), `python check.py`. Новий продавець без категорії — рядок у `rules.csv`.

## 6. Безпека

- **Без входу жодних даних.** Кожна сторінка, включно з Fava, без сесії веде на вхід; API Fava віддає 401.
- **Секрети лише в середовищі.** `TRACKER_SECRET_KEY` і `TRACKER_USERS` читаються з `.env` на сервері (він у `.gitignore`). У репозиторії немає ні паролів, ні хешів.
- **Паролі — лише scrypt-хеші** (`werkzeug.security`); зміна пароля скасовує старі сесії.
- **Лише HTTPS.** Force HTTPS на PythonAnywhere + редірект http→https у застосунку + HSTS. Cookie `Secure`, `HttpOnly`, `SameSite=Lax`.
- **Форми**: CSRF-токен на всіх POST; не більше 5 невдалих входів з IP за 15 хв і 10 на логін за годину; `Cache-Control: no-store`, CSP без скриптів, `X-Frame-Options: DENY`, `noindex`.
- **Облік не зламати з телефона**: якщо новий запис дає помилку Beancount, файл повертається до попереднього стану.

Двофакторного входу і шифрування полів немає свідомо: користувачів двоє, сервер бачить лише Оля, а 2FA для Марка — ще одна причина перестати відкривати трекер.

## 7. Налаштування агента

`docs/claude-setup-prompt.md` — готовий промпт для Claude Code, який проводить через усі кроки вище.
