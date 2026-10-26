"""Точка входу WSGI. Секрети беруться зі змінних середовища або файлу .env поруч."""

import logging

from tracker.web import create_app

logging.basicConfig(level=logging.INFO)
application = create_app()
