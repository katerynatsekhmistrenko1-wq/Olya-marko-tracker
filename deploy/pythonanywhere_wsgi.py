# Вміст WSGI-файлу на PythonAnywhere (вкладка Web → «WSGI configuration file»).
# Замініть USERNAME на свій логін PythonAnywhere. Секретів тут НЕМАЄ:
# вони лежать у ~/olya-marko-tracker/.env (див. README, крок 4).

import sys

PROJECT = "/home/USERNAME/olya-marko-tracker"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from wsgi import application  # noqa: E402,F401
