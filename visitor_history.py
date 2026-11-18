"""История показанных материалов для анонимных посетителей.

Идентификатор посетителя хранится в устойчивом cookie. Сама история
хранится в JSON-файле на сервере (не в памяти процесса), чтобы переживать
перезапуск обработчика в рамках одного инстанса. Учётных записей в
проекте нет, поэтому разделение идёт по visitor id.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Request

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY_FILE = BASE_DIR / "data" / "visitor_history.json"
VISITOR_COOKIE = "digest_vid"
VISITOR_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_HISTORY = 200

_lock = threading.Lock()


def history_path() -> Path:
    configured = os.environ.get("DIGEST_HISTORY_FILE", "").strip()
    path = Path(configured) if configured else DEFAULT_HISTORY_FILE
    return path if path.is_absolute() else BASE_DIR / path


def get_visitor_id(request: Request) -> tuple[str, bool]:
    """Возвращает (id, is_new). Новый id вызывающая сторона запишет в cookie."""
    candidate = (request.cookies.get(VISITOR_COOKIE) or "").strip().lower()
    if VISITOR_ID_RE.fullmatch(candidate):
        return candidate, False
    return uuid.uuid4().hex, True


def _read_all() -> dict:
    path = history_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_seen(visitor_id: str) -> set[str]:
    entry = _read_all().get(visitor_id)
    if isinstance(entry, dict):
        keys = entry.get("seen", [])
    elif isinstance(entry, list):
        keys = entry
    else:
        keys = []
    if not isinstance(keys, list):
        return set()
    return {str(key) for key in keys if isinstance(key, str)}


def add_seen(visitor_id: str, keys) -> None:
    """Добавляет ключи в историю; идемпотентно и без блокировок на чтение."""
    key_list = [str(key) for key in keys if isinstance(key, str)]
    if not key_list:
        return

    with _lock:
        path = history_path()
        data = _read_all()
        entry = data.get(visitor_id)
        if isinstance(entry, dict):
            seen = [str(key) for key in entry.get("seen", []) if isinstance(key, str)]
        elif isinstance(entry, list):
            seen = [str(key) for key in entry if isinstance(key, str)]
        else:
            seen = []

        known = set(seen)
        added = False
        for key in key_list:
            if key not in known:
                seen.append(key)
                known.add(key)
                added = True

        if not added:
            return

        seen = seen[-MAX_HISTORY:]
        data[visitor_id] = {
            "seen": seen,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp, path)
        except OSError:
            # История — улучшение UX, а не критичный путь. Если файловая
            # система доступна только на чтение, сайт продолжает работать.
            return
