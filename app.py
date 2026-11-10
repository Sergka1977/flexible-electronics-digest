"""Гибкий слой — Flask-дайджест гибкой электроники.

Запуск:
    python app.py
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "news.json"
SUBSCRIBERS_FILE = BASE_DIR / "data" / "subscribers.json"

RU_MONTHS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

app = Flask(__name__)


def load_digest() -> dict:
    """Читает дайджест из JSON-файла."""
    with DATA_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def format_ru_date(value: str) -> str:
    """Форматирует ISO-дату по-русски: 9 ноября 2026."""
    parsed = date.fromisoformat(value)
    return f"{parsed.day} {RU_MONTHS[parsed.month]} {parsed.year}"


def plural_ru(number: int, one: str, few: str, many: str) -> str:
    """Русская форма существительного для числа."""
    remainder = abs(number) % 100
    if 11 <= remainder <= 14:
        return many
    remainder %= 10
    if remainder == 1:
        return one
    if 2 <= remainder <= 4:
        return few
    return many


def format_period(start_value: str, end_value: str) -> str:
    """Форматирует период выпуска."""
    start = date.fromisoformat(start_value)
    end = date.fromisoformat(end_value)
    if start.year == end.year:
        return (
            f"{start.day} {RU_MONTHS[start.month]} — "
            f"{end.day} {RU_MONTHS[end.month]} {end.year}"
        )
    return (
        f"{start.day} {RU_MONTHS[start.month]} {start.year} — "
        f"{end.day} {RU_MONTHS[end.month]} {end.year}"
    )


def prepare_items(raw_items: list[dict], meta: dict) -> tuple[list[dict], bool]:
    """Нормализует карточки: порядок, даты, номера и проверка окна."""
    # Сначала фиксируем редакционный порядок, затем сортируем по дате (новые выше).
    items = sorted(raw_items, key=lambda item: item.get("order", 0))
    items.sort(key=lambda item: item["date"], reverse=True)

    start = date.fromisoformat(meta["period_start"])
    end = date.fromisoformat(meta["period_end"])

    for index, item in enumerate(items, start=1):
        item["date_display"] = format_ru_date(item["date"])
        item["number"] = index

    dates_in_window = all(
        start <= date.fromisoformat(item["date"]) <= end for item in items
    )
    return items, dates_in_window


def build_page_data(batch: int = 1) -> dict:
    """Готовит данные для шаблона и проверяет целостность выпуска."""
    digest = load_digest()
    meta = digest["meta"]
    filters = digest["filters"]
    refresh = digest.get("refresh", {})

    if batch == 2:
        raw_items = refresh.get("items", [])
        target_count = int(refresh.get("target_count", len(raw_items)))
    else:
        raw_items = digest["items"]
        target_count = int(meta["target_count"])

    items, dates_in_window = prepare_items(raw_items, meta)
    is_complete = len(items) == target_count and dates_in_window

    # Считаем покрытие тем: столько карточек покажет каждый фильтр.
    counts = {}
    for item_filter in filters:
        slug = item_filter["slug"]
        if slug == "all":
            counts[slug] = len(items)
        else:
            counts[slug] = sum(1 for item in items if slug in item["topics"])

    research_topics = [
        item_filter
        for item_filter in filters
        if item_filter["slug"] not in {"all", "jobs"}
    ]
    start = date.fromisoformat(meta["period_start"])
    end = date.fromisoformat(meta["period_end"])
    period_days = (end - start).days + 1

    return {
        "digest": digest,
        "meta": meta,
        "items": items,
        "filters": filters,
        "counts": counts,
        "organizations": digest.get("organizations", []),
        "vacancy_links": meta.get("vacancy_links", []),
        "is_complete": is_complete,
        "dates_in_window": dates_in_window,
        "period_display": format_period(meta["period_start"], meta["period_end"]),
        "compiled_display": format_ru_date(meta["compiled_at"]),
        "target_count": target_count,
        "batch": batch,
        "stats": {
            "items": len(items),
            "topics": len(research_topics),
            "days": period_days,
            "days_label": plural_ru(period_days, "день", "дня", "дней"),
        },
        "sources": list(dict.fromkeys(item["source"] for item in items)),
    }


@app.get("/")
def index():
    """Главная страница дайджеста."""
    return render_template("index.html", **build_page_data())


@app.get("/health")
def health():
    """Небольшой служебный эндпоинт для проверки данных."""
    main = build_page_data(1)
    refreshed = build_page_data(2)
    return jsonify(
        ok=True,
        items=len(main["items"]),
        complete=main["is_complete"],
        dates_in_window=main["dates_in_window"],
        refresh_items=len(refreshed["items"]),
        refresh_complete=refreshed["is_complete"],
        refresh_dates_in_window=refreshed["dates_in_window"],
    )


@app.get("/api/refresh")
def refresh_digest():
    """Возвращает другую подборку за тот же период и по тем же темам."""
    batch = request.args.get("batch", type=int)
    if batch is None:
        batch = 2
    if batch not in (1, 2):
        return jsonify(ok=False, message="Доступны партии batch=1 и batch=2."), 400

    data = build_page_data(batch)
    response = jsonify(
        ok=True,
        batch=batch,
        next_batch=1 if batch == 2 else 2,
        target_count=data["target_count"],
        is_complete=data["is_complete"],
        dates_in_window=data["dates_in_window"],
        period_display=data["period_display"],
        counts=data["counts"],
        stats=data["stats"],
        sources=data["sources"],
        items=data["items"],
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/subscribe")
def subscribe():
    """Сохраняет email локально (демо-режим, без отправки писем)."""
    payload = request.get_json(silent=True) or request.form
    email = str(payload.get("email", "")).strip().lower()

    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify(ok=False, message="Проверьте формат email и попробуйте ещё раз."), 400

    subscribers: list[str] = []
    if SUBSCRIBERS_FILE.exists():
        try:
            loaded = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                subscribers = [str(value) for value in loaded]
        except (json.JSONDecodeError, OSError):
            subscribers = []

    if email not in subscribers:
        subscribers.append(email)
        try:
            SUBSCRIBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SUBSCRIBERS_FILE.write_text(
                json.dumps(subscribers, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return (
                jsonify(
                    ok=False,
                    message="Не удалось сохранить подписку. Попробуйте позже.",
                ),
                500,
            )

    return jsonify(
        ok=True,
        message="Готово! Адрес сохранён. Это демо-режим: автоматическая рассылка пока не подключена.",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
