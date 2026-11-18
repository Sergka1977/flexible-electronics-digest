"""Гибкий слой — Flask-дайджест гибкой электроники.

Локальный запуск:
    python app.py

Production-запуск (в том числе Render Free):
    gunicorn --bind 0.0.0.0:$PORT app:app
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, make_response, render_template, request

import news_sources
import visitor_history

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "news.json"

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
COOKIE_MAX_AGE = 365 * 24 * 60 * 60
REFRESH_TARGET_COUNT = 10

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


def format_period_range(start_value: str, end_value: str) -> str:
    """Формат для фразы «выпуск охватывает срок с … по …»."""
    start = date.fromisoformat(start_value)
    end = date.fromisoformat(end_value)
    if start.year == end.year:
        return (
            f"с {start.day} {RU_MONTHS[start.month]} "
            f"по {end.day} {RU_MONTHS[end.month]} {end.year}"
        )
    return (
        f"с {start.day} {RU_MONTHS[start.month]} {start.year} "
        f"по {end.day} {RU_MONTHS[end.month]} {end.year}"
    )


def prepare_items(raw_items: list[dict], start: date, end: date) -> tuple[list[dict], bool]:
    """Нормализует карточки: порядок, даты, номера и проверка окна."""
    # Сначала фиксируем редакционный порядок, затем сортируем по дате (новые выше).
    # Стабильная сортировка по рангу TFT поднимает релевантные материалы,
    # не меняя порядок остальных карточек внутри своей группы.
    items = sorted(raw_items, key=lambda item: item.get("order", 0))
    items.sort(key=lambda item: item["date"], reverse=True)
    items.sort(key=lambda item: item.get("tft_rank", 3))

    for index, item in enumerate(items, start=1):
        item["date_display"] = format_ru_date(item["date"])
        item["number"] = index

    dates_in_window = all(
        start <= date.fromisoformat(item["date"]) <= end for item in items
    )
    return items, dates_in_window


def build_payload(
    raw_items: list[dict],
    start: date,
    end: date,
    target_count: int,
    meta: dict,
    filters: list[dict],
) -> dict:
    """Общие показатели страницы для любого набора карточек."""
    items, dates_in_window = prepare_items(raw_items, start, end)
    is_complete = len(items) == target_count and dates_in_window

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
    period_days = (end - start).days + 1

    return {
        "items": items,
        "counts": counts,
        "is_complete": is_complete,
        "dates_in_window": dates_in_window,
        "period_display": format_period(start.isoformat(), end.isoformat()),
        "period_range": format_period_range(start.isoformat(), end.isoformat()),
        "target_count": target_count,
        "stats": {
            "items": len(items),
            "topics": len(research_topics),
            "days": period_days,
            "days_label": plural_ru(period_days, "день", "дня", "дней"),
        },
        "sources": list(dict.fromkeys(item["source"] for item in items)),
    }


def build_page_data(batch: int = 1) -> dict:
    """Данные исходного выпуска (batch=1/2 сохраняются для /health)."""
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

    # И стартовый выпуск, и обновление показывают одно и то же скользящее окно:
    # сегодня минус один календарный месяц … сегодня. Даты из JSON не используем.
    start, end = news_sources.sliding_period()
    payload = build_payload(raw_items, start, end, target_count, meta, filters)

    return {
        "digest": digest,
        "meta": meta,
        "filters": filters,
        "organizations": digest.get("organizations", []),
        "vacancy_links": meta.get("vacancy_links", []),
        "compiled_display": format_ru_date(meta["compiled_at"]),
        "batch": batch,
        **payload,
    }


def set_visitor_cookie(response, visitor_id: str, is_new: bool):
    """Закрепляет устойчивый идентификатор анонимного посетителя."""
    if is_new:
        response.set_cookie(
            visitor_history.VISITOR_COOKIE,
            visitor_id,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
        )
    response.headers["Vary"] = "Cookie"
    return response


@app.get("/")
def index():
    """Главная страница дайджеста.

    Исходные карточки тоже считаются показанными: они попадают в историю,
    чтобы следующее обновление не вернуло их повторно.
    """
    data = build_page_data()
    visitor_id, is_new = visitor_history.get_visitor_id(request)
    visitor_history.add_seen(
        visitor_id, [news_sources.item_key(item) for item in data["items"]]
    )

    response = make_response(render_template("index.html", **data))
    return set_visitor_cookie(response, visitor_id, is_new)


@app.get("/health")
def health():
    """Служебная проверка данных и подключённых источников."""
    main = build_page_data(1)
    refreshed = build_page_data(2)
    start, end = news_sources.sliding_period()
    return jsonify(
        ok=True,
        items=len(main["items"]),
        complete=main["is_complete"],
        dates_in_window=main["dates_in_window"],
        refresh_items=len(refreshed["items"]),
        refresh_complete=refreshed["is_complete"],
        refresh_dates_in_window=refreshed["dates_in_window"],
        mode="live",
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        live_sources=[name for name, _ in news_sources.FETCHERS],
        vacancy_sources=[name for name, _ in news_sources.JOB_FETCHERS],
        history_file=str(visitor_history.history_path()),
    )


def _requested_tag(raw_tag, filters: list[dict]) -> str:
    """Возвращает тег, выбранный в интерфейсе в момент нажатия кнопки."""
    candidate = str(raw_tag or "all").strip().lower()
    known = {item["slug"] for item in filters}
    return candidate if candidate in known else "all"


def _tag_label(tag: str, filters: list[dict]) -> str:
    for item in filters:
        if item["slug"] == tag:
            return item["label"]
    return "Все"


def _merge_collected(collected: dict, extra: dict) -> dict:
    """Добавляет результаты дополнительного поиска без дубликатов."""
    known = {item["id"] for item in collected["items"]}
    for item in extra["items"]:
        if item["id"] in known:
            continue
        known.add(item["id"])
        collected["items"].append(item)
    collected["errors"] = list(dict.fromkeys(collected["errors"] + extra["errors"]))
    collected["sources"] = list(dict.fromkeys(collected["sources"] + extra["sources"]))
    return collected


def _refresh_message(
    tag: str, label: str, count: int, target: int, period: str, shortfall: bool
) -> str:
    noun = "вакансий" if tag == "jobs" else "публикаций"
    scope = "по всем темам" if tag == "all" else f"по тегу «{label}»"
    if shortfall:
        return (
            f"Найдено {count} из {target} подходящих {noun} {scope} за период "
            f"{period}. Это меньше {target}: дополнительный поиск в тех же "
            "источниках и периоде не дал новых релевантных материалов, поэтому "
            "подборка не дополнялась неподходящими или уже показанными записями."
        )
    return (
        f"Подборка обновлена: {count} новых {noun} {scope} за период {period}."
    )


@app.get("/api/refresh")
def refresh_digest():
    """Новый поиск по тегу, который был активен при нажатии кнопки.

    Для «Все» материалы выбираются случайно с чередованием тем, для
    конкретного тега — только по его содержанию, для «Вакансий» — только
    реальные вакансии из подключённых keyless API. Повторно показанные
    материалы исключаются по истории посетителя. Если все источники
    недоступны, текущая подборка остаётся на странице.
    """
    digest = load_digest()
    meta = digest["meta"]
    filters = digest["filters"]
    target = REFRESH_TARGET_COUNT
    tag = _requested_tag(request.args.get("tag"), filters)
    label = _tag_label(tag, filters)

    visitor_id, is_new = visitor_history.get_visitor_id(request)
    seen_keys = visitor_history.load_seen(visitor_id)

    # Период считается заново на каждый запрос, без зашитых дат.
    start, end = news_sources.sliding_period()
    collected = news_sources.collect_candidates(topic=tag)
    all_sources_failed = bool(collected["errors"]) and not collected["items"]

    def choose():
        return news_sources.select_items(
            collected["items"], seen_keys, target, start, end, topic=tag
        )

    selected = choose()

    # Если подходящих меньше десяти, пробуем ещё один поиск в тех же
    # источниках и том же периоде с другими поисковыми формулировками.
    if len(selected) < target and not all_sources_failed:
        extra = news_sources.collect_candidates(topic=tag, fallback=True)
        collected = _merge_collected(collected, extra)
        all_sources_failed = bool(collected["errors"]) and not collected["items"]
        selected = choose()

    if not selected:
        if all_sources_failed:
            reason = "source_unavailable"
            if tag == "jobs":
                message = (
                    "Источники вакансий временно недоступны. "
                    "Текущая подборка сохранена — попробуйте позже."
                )
            else:
                message = (
                    "Источники новостей временно недоступны. "
                    "Текущая подборка сохранена — попробуйте позже."
                )
            status = 503
        else:
            reason = "no_new_items"
            if tag == "jobs":
                message = (
                    "За последний месяц не найдено подходящих вакансий по тематике "
                    "сайта. Текущая подборка сохранена; актуальные позиции смотрите "
                    "на карьерных страницах организаций."
                )
            elif tag == "all":
                message = (
                    "Новых публикаций за последний месяц не осталось. "
                    "Текущая подборка сохранена."
                )
            else:
                message = (
                    f"По тегу «{label}» за последний месяц не найдено новых "
                    "подходящих материалов. Текущая подборка сохранена."
                )
            status = 200

        response = make_response(
            jsonify(
                ok=False,
                reason=reason,
                message=message,
                kept_current=True,
                active_tag=tag,
                errors=collected["errors"],
            ),
            status,
        )
        response.headers["Cache-Control"] = "no-store"
        return set_visitor_cookie(response, visitor_id, is_new)

    visitor_history.add_seen(visitor_id, [item["id"] for item in selected])
    payload = build_payload(selected, start, end, target, meta, filters)
    count = payload["stats"]["items"]

    response = make_response(
        jsonify(
            ok=True,
            mode="live",
            active_tag=tag,
            tag_label=label,
            target_count=payload["target_count"],
            is_complete=payload["is_complete"],
            shortfall=count < target,
            dates_in_window=payload["dates_in_window"],
            period_display=payload["period_display"],
            period_range=payload["period_range"],
            counts=payload["counts"],
            stats=payload["stats"],
            sources=collected["sources"] if tag == "jobs" else payload["sources"],
            items=payload["items"],
            message=_refresh_message(
                tag, label, count, target, payload["period_display"], count < target
            ),
            errors=collected["errors"],
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return set_visitor_cookie(response, visitor_id, is_new)


@app.post("/api/subscribe")
def subscribe():
    """Демо-режим: проверяет email, но ничего не сохраняет и не отправляет."""
    payload = request.get_json(silent=True) or request.form
    email = str(payload.get("email", "")).strip().lower()

    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify(ok=False, message="Проверьте формат email и попробуйте ещё раз."), 400

    return jsonify(
        ok=True,
        message=(
            "Подписка пока демонстрационная: адрес не сохраняется, "
            "постоянное сохранение и рассылка ещё не подключены."
        ),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
