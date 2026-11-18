"""Реальные источники новостей и подготовка карточек дайджеста.

Используются только открытые публичные API без обязательных ключей:
Crossref, OpenAlex и arXiv. Модуль не придумывает публикации: карточка
строится исключительно из метаданных, полученных от источника.
"""

from __future__ import annotations

import hashlib
import html
import inspect
import json
import math
import os
import random
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

USER_AGENT = "FlexibleElectronicsDigest/2.0 (open-source digest; no bulk crawling)"

TOPIC_KEYWORDS = {
    "flexible": (
        r"flexible\s+electronics?",
        r"stretchable",
        r"bendable",
        r"foldable",
        r"conformable",
        r"soft\s+electronics?",
        r"electronic\s+textiles?",
        r"e-?textiles?",
        r"skin[- ]mounted",
        r"strain\s+sensor",
    ),
    "printed": (
        r"print(?:ed|ing)\b",
        r"inkjet",
        r"screen[- ]print",
        r"aerosol\s+jet",
        r"additive\s+manufactur",
        r"3d\s+print",
        r"conductive\s+ink",
    ),
    "wearable": (
        r"wearable",
        r"on[- ]skin",
        r"skin[- ]like",
        r"sweat",
        r"textile",
        r"garment",
        r"patch",
        r"motion\s+monitor",
        r"human\s+movement",
        r"physiological\s+monitor",
    ),
    "materials": (
        r"hydrogel",
        r"graphene",
        r"mxene",
        r"pedot",
        r"perovskite",
        r"nanomaterial",
        r"nanowire",
        r"nanoflake",
        r"liquid\s+metal",
        r"composite",
        r"polymer",
        r"semiconductor",
    ),
    "displays": (
        r"display",
        r"electrochromic",
        r"electrophoretic",
        r"\bols?ed\b",
        r"\bleds?\b",
        r"pixel",
        r"screen",
    ),
    "medicine": (
        r"biosensor",
        r"biomedical",
        r"medical",
        r"healthcare",
        r"health\s+monitor",
        r"diagnos",
        r"clinical",
        r"\beeg\b",
        r"\becg\b",
        r"glucose",
        r"biomarker",
        r"disease",
    ),
}

TOPIC_TAGS = {
    "flexible": "Гибкая электроника",
    "printed": "Печатная электроника",
    "wearable": "Носимые сенсоры",
    "materials": "Материалы",
    "displays": "Гибкие дисплеи",
    "tft": "TFT matrixes",
    "medicine": "Медицина",
    "jobs": "Вакансии",
}

TOPIC_STYLE = {
    "flexible": ("FLEX", "cyan"),
    "printed": ("PRINT", "blue"),
    "wearable": ("WEAR", "teal"),
    "materials": ("MAT", "indigo"),
    "displays": ("DISP", "indigo"),
    "tft": ("TFT", "blue"),
    "medicine": ("MED", "teal"),
    "jobs": ("JOBS", "cyan"),
}

# Темы сайта, по которым ищутся публикации и вакансии.
RESEARCH_SLUGS = (
    "flexible",
    "printed",
    "wearable",
    "materials",
    "displays",
    "tft",
    "medicine",
)

JUNK_TITLE_RE = re.compile(
    r"^(front\s+cover|back\s+cover|cover|table\s+of\s+contents|"
    r"publication\s+information|editorial\s+board|masthead|"
    r"corrigendum|erratum|author\s+index|subject\s+index)\b",
    re.IGNORECASE,
)

# Признаки темы TFT matrixes. Релевантность назначается только при явном
# упоминании TFT/backplane/активной матрицы или TFT-материала вместе с
# дисплейным/матричным контекстом — общие новости о дисплеях не подходят.
TFT_SPECIFIC_RE = re.compile(
    r"\btfts?\b|tft[- ]?(?:matrix|matrices|array|backplane|panel|display|device)s?\b|"
    r"thin[- ]?film transistors?\b|\botfts?\b|active[- ]matrix\b|"
    r"\bamoled\b|oxide[- ]?tft|printed[- ]?tft|a[- ]?si[- ]?tft|"
    r"тонкопленочн\w*\s+транзистор|tft[- ]?матриц",
    re.IGNORECASE,
)
TFT_MATRIX_RE = re.compile(
    r"\bmatrix\b|\bmatrices\b|\barray\b|\bbackplane\b|pixel[- ]?(?:array|matrix)|"
    r"thin[- ]?film transistor|матриц|бэкплейн",
    re.IGNORECASE,
)
TFT_FLEX_RE = re.compile(
    r"flexible|bendable|foldable|stretchable|rollable|conformable|"
    r"polyimide|pi\s+substrate|pet\s+substrate|pen\s+substrate|"
    r"polyethylene\s+(?:terephthalate|naphthalate)|plastic\s+substrate|polymer\s+substrate|"
    r"гибк|изгиб|сгиба|полиимид",
    re.IGNORECASE,
)
TFT_DISPLAY_RE = re.compile(
    r"\blcd\b|\boled\b|\bamoled\b|micro[- ]?led|electronic\s+paper|e[- ]?ink|"
    r"display|pixel|screen|диспле|экран|панел",
    re.IGNORECASE,
)
TFT_BACKLIGHT_RE = re.compile(
    r"backlight|mini[- ]?led|local\s+dimming|\bdimming\b|подсветк|затемнен",
    re.IGNORECASE,
)
TFT_MATERIAL_RE = re.compile(
    r"\bigzo\b|\bltps\b|oxide\s+semiconductor|amorphous\s+silicon|a[- ]?si\b|"
    r"organic\s+semiconductor|metal\s+oxide|индий[‑-]?галлий|оксидн|аморфн",
    re.IGNORECASE,
)
TFT_PROCESS_RE = re.compile(
    r"print(?:ed|ing)\b|inkjet|laser\s+transfer|roll[- ]to[- ]roll|"
    r"photolithograph|etching|anneal|transfer\s+process|печат|перенос",
    re.IGNORECASE,
)

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Поисковые запросы под каждый тег. Один основной запрос на тему плюс
# уточняющий для TFT: так «Все» получает смесь всех тем, а конкретный тег —
# только свои метаданные. Дополнительные запросы используются, если после
# первого прохода найдено меньше нужного количества материалов.
TOPIC_QUERIES = {
    "flexible": ("flexible electronics strain sensor",),
    "printed": ("printed electronics conductive ink",),
    "wearable": ("wearable biosensor hydrogel",),
    "materials": ("hydrogel MXene flexible electronic materials",),
    "displays": ("flexible display electrochromic pixel",),
    "tft": (
        "flexible thin film transistor backplane",
        "TFT array display IGZO",
    ),
    "medicine": ("wearable medical biosensor health monitoring",),
}

TOPIC_FALLBACK_QUERIES = {
    "flexible": ("stretchable bendable conformable electronics",),
    "printed": ("screen printed electronics inkjet conductive ink",),
    "wearable": ("on-skin sweat sensor electronic textile",),
    "materials": ("graphene perovskite organic semiconductor electronics",),
    "displays": ("electrochromic display flexible substrate OLED",),
    "tft": (
        "TFT backplane active matrix display",
        "oxide IGZO thin film transistor circuit",
    ),
    "medicine": ("flexible biosensor clinical diagnostics patch",),
}

ALL_QUERIES = tuple(
    dict.fromkeys(query for queries in TOPIC_QUERIES.values() for query in queries)
)
ALL_FALLBACK_QUERIES = tuple(
    dict.fromkeys(
        query for queries in TOPIC_FALLBACK_QUERIES.values() for query in queries
    )
)

CROSSREF_QUERIES = ALL_QUERIES
OPENALEX_QUERIES = ALL_QUERIES
ARXIV_QUERIES = ALL_QUERIES

# Открытые keyless API вакансий. Оба источника отдают только реальные вакансии
# с датой и прямой ссылкой; релевантность дополнительно проверяется локально.
JOB_QUERIES = (
    "flexible electronics",
    "printed electronics",
    "wearable sensor",
    "thin film transistor",
    "semiconductor materials",
)

JOB_STRONG_RE = re.compile(
    r"flexible\s+electron|printed\s+electron|wearable\s+(?:sensor|device)|"
    r"thin[- ]?film\s+transistor|\btfts?\b|tft[- ]?(?:matrix|array|backplane)|"
    r"active[- ]matrix|electrochromic|electronic\s+textile|e[- ]?textile|"
    r"biosensor|hydrogel|mxene|perovskite|organic\s+semiconductor|"
    r"semiconductor\s+(?:material|device|manufactur|process|equipment|industry)|"
    r"display\s+backplane",
    re.IGNORECASE,
)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def subtract_calendar_month(value: date) -> date:
    """Отнимает один календарный месяц, корректно обрабатывая конец месяца."""
    year = value.year if value.month > 1 else value.year - 1
    month = value.month - 1 if value.month > 1 else 12
    last_day = _last_day_of_month(year, month)
    return date(year, month, min(value.day, last_day))


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def sliding_period(today: date | None = None) -> tuple[date, date]:
    """Скользящее окно: сегодня минус один календарный месяц … сегодня."""
    end = today or date.today()
    return subtract_calendar_month(end), end


def strip_markup(value: str) -> str:
    """Убирает JATS/HTML-разметку из метаданных, не меняя смысл текста."""
    if not value:
        return ""
    # Часть API отдаёт разметку в экранированном виде (&lt;div&gt;), поэтому
    # сначала декодируем сущности, затем убираем теги.
    text = html.unescape(value)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def clip_text(value: str, limit: int = 620) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def normalize_title(value: str) -> str:
    """Нормализованный заголовок — резервный идентификатор, когда нет URL."""
    text = unicodedata.normalize("NFKC", value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(url: str = "", doi: str = "") -> str:
    """Приводит URL или DOI к каноническому виду для сравнения дубликатов."""
    doi_value = (doi or "").strip().lower()
    if doi_value.startswith("https://doi.org/"):
        doi_value = doi_value[len("https://doi.org/") :]
    if doi_value.startswith("doi:"):
        doi_value = doi_value[4:]
    if doi_value:
        return "https://doi.org/" + doi_value.strip("/")

    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    parts = urlsplit(raw)
    if not parts.scheme:
        raw = "https://" + raw.lstrip("/")
        parts = urlsplit(raw)

    scheme = "https" if parts.scheme in {"http", "https"} else parts.scheme.lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Регистр в путях DOI/препринтов не несёт смысла для сравнения дубликатов.
    path = parts.path.rstrip("/").lower() or ""

    if host in {"doi.org", "dx.doi.org"} and path:
        return "https://doi.org/" + path.lstrip("/")

    if "arxiv.org" in host:
        match = re.search(r"/(abs|pdf)/([^/]+?)(?:\.pdf)?$", path, re.IGNORECASE)
        if match:
            arxiv_id = re.sub(r"v\d+$", "", match.group(2))
            path = f"/abs/{arxiv_id}"

    return f"{scheme}://{host}{path}"


def item_key(item: dict) -> str:
    """Стабильный ключ: канонический URL, иначе нормализованный заголовок."""
    basis = canonical_url(item.get("url", ""), item.get("doi", ""))
    if not basis:
        basis = "title:" + normalize_title(item.get("title", ""))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def queries_for_topic(topic: str, fallback: bool = False) -> tuple[str, ...]:
    """Возвращает поисковые фразы для активного тега (или для «Все»)."""
    if topic == "jobs":
        return JOB_QUERIES if not fallback else JOB_QUERIES[::-1]
    source = TOPIC_FALLBACK_QUERIES if fallback else TOPIC_QUERIES
    if topic in source:
        return source[topic]
    return ALL_FALLBACK_QUERIES if fallback else ALL_QUERIES


def is_vacancy(item: dict) -> bool:
    """Признак вакансии: её создаёт только fetcher вакансий."""
    return bool(item.get("is_vacancy")) and "jobs" in item.get("topics", [])


def is_relevant_vacancy(title: str, description: str) -> set[str]:
    """Проверяет вакансию по содержанию и возвращает совпавшие темы сайта."""
    text = strip_markup(f"{title or ''} {description or ''}")
    # Одно случайное упоминание (например, «wearable devices» в вакансии
    # спортивного софта) недостаточно: нужны минимум два независимых
    # подтверждения связи с тематикой сайта.
    if len(JOB_STRONG_RE.findall(text)) < 2:
        return set()
    matched = set()
    for slug in RESEARCH_SLUGS:
        patterns = TOPIC_KEYWORDS.get(slug)
        if not patterns:
            continue
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            matched.add(slug)
    return matched


def matches_topic(item: dict, topic: str) -> bool:
    """Проверяет соответствие карточки выбранному тегу по содержанию.

    Для «Все» подходят все релевантные публикации. Для конкретного тега
    недостаточно совпадения в заголовке: тема уже должна быть определена
    classify_topics по заголовку и абстракту из метаданных источника.
    """
    if topic == "all":
        return True
    if topic == "jobs":
        return is_vacancy(item)
    topics = item.get("topics", [])
    if topic not in topics:
        return False
    if topic == "tft":
        return tft_relevance(item.get("title", ""), item.get("summary", "")) is not None
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    patterns = TOPIC_KEYWORDS.get(topic, ())
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _call_fetcher(fetcher, today, timeout, per_query, queries):
    """Вызывает fetcher с запросами, сохраняя совместимость с простыми заглушками."""
    try:
        accepts_queries = len(inspect.signature(fetcher).parameters) >= 4
    except (TypeError, ValueError):
        accepts_queries = False
    if accepts_queries:
        return fetcher(today, timeout, per_query, queries)
    return fetcher(today, timeout, per_query)


def _diversify(items: list[dict], target_count: int, rng) -> list[dict]:
    """Случайный отбор с чередованием тем, чтобы «Все» не сводился к одной теме."""
    buckets: dict[str, list[dict]] = {}
    for item in items:
        topics = [
            slug
            for slug in item.get("topics", [])
            if slug not in {"all", "jobs"} and slug in RESEARCH_SLUGS
        ]
        primary = topics[0] if topics else "other"
        buckets.setdefault(primary, []).append(item)

    keys = list(buckets)
    rng.shuffle(keys)
    for key in keys:
        rng.shuffle(buckets[key])
        if key == "tft":
            buckets[key].sort(key=lambda value: value.get("tft_rank", 3))

    selected: list[dict] = []
    while len(selected) < target_count and any(buckets.values()):
        for key in keys:
            if len(selected) >= target_count:
                break
            if buckets[key]:
                selected.append(buckets[key].pop(0))
    return selected


def tft_relevance(title: str, summary: str = "") -> tuple[int, str] | None:
    """Возвращает (приоритет, пояснение) для темы TFT matrixes.

    0 — TFT-матрица/backplane вместе с гибкой подложкой или дисплеем;
    1 — матрица/активная подсветка для дисплеев без гибкой подложки;
    2 — материалы и процессы TFT без подтверждённой матрицы в метаданных.
    """
    title_text = title or ""
    text = f"{title_text} {summary or ''}"
    specific = bool(TFT_SPECIFIC_RE.search(text))
    matrix = bool(TFT_MATRIX_RE.search(text))
    display = bool(TFT_DISPLAY_RE.search(text))
    backlight = bool(TFT_BACKLIGHT_RE.search(text))
    material = bool(TFT_MATERIAL_RE.search(text))
    process = bool(TFT_PROCESS_RE.search(text))

    title_specific = bool(TFT_SPECIFIC_RE.search(title_text))
    title_matrix = bool(TFT_MATRIX_RE.search(title_text))
    title_flex = bool(TFT_FLEX_RE.search(title_text))
    title_display = bool(TFT_DISPLAY_RE.search(title_text))
    title_backlight = bool(TFT_BACKLIGHT_RE.search(title_text))

    # Без явного TFT/backplane/активной матрицы или TFT-материала в
    # дисплейном/технологическом контексте материал к теме не относится.
    if not specific and not (
        material and (matrix or display or backlight or process)
    ):
        return None

    # Высший приоритет: оба признака видны уже в заголовке — TFT/матрица
    # и гибкая подложка/дисплей.
    if (title_specific or title_matrix) and title_flex and (
        title_display or title_matrix or title_backlight or title_specific
    ):
        return 0, ""

    if (matrix or specific) and (
        title_display or title_backlight or display or backlight
    ):
        if specific:
            return 1, ""
        return (
            1,
            "Косвенная связь: метаданные описывают активную матрицу или подсветку, "
            "но не упоминают TFT напрямую.",
        )

    if material or process:
        return (
            2,
            "Косвенная связь: метаданные описывают материал или технологию TFT; "
            "готовая матрица или дисплей в них не подтверждены.",
        )

    return None


def classify_topics(title: str, summary: str = "") -> list[str]:
    """Определяет темы выпуска по ключевым словам исходных метаданных."""
    text = f"{title or ''} {summary or ''}".lower()
    topics = []
    for slug, patterns in TOPIC_KEYWORDS.items():
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            topics.append(slug)

    if tft_relevance(title, summary):
        topics.insert(0, "tft")
    return topics


def _date_from_parts(parts) -> str | None:
    if not isinstance(parts, list) or not parts:
        return None
    values = parts[0]
    if not isinstance(values, list) or len(values) < 3:
        return None
    try:
        return date(int(values[0]), int(values[1]), int(values[2])).isoformat()
    except (TypeError, ValueError):
        return None


def _crossref_date(item: dict) -> str | None:
    for field in ("published", "published-online", "published-print"):
        value = _date_from_parts((item.get(field) or {}).get("date-parts"))
        if value:
            return value
    return None


def _topics_card_fields(topics: list[str]) -> dict:
    primary = topics[0]
    visual, tone = TOPIC_STYLE.get(primary, ("NEWS", "cyan"))
    return {
        "topics": topics,
        "tags": [TOPIC_TAGS[slug] for slug in topics[:4] if slug in TOPIC_TAGS],
        "visual": visual,
        "tone": tone,
    }


def _make_card(
    *,
    title: str,
    summary: str,
    source: str,
    via: str,
    published: str,
    url: str,
    doi: str = "",
    topics: list[str] | None = None,
) -> dict | None:
    title = clip_text(strip_markup(title), 240)
    summary = clip_text(strip_markup(summary))
    if not title or JUNK_TITLE_RE.search(title):
        return None

    if topics is None:
        topics = classify_topics(title, summary)
    else:
        topics = list(topics)

    relevance = tft_relevance(title, summary)
    if relevance and "tft" not in topics:
        topics.insert(0, "tft")
    if not topics:
        return None

    canonical = canonical_url(url, doi)
    if not canonical:
        return None

    style = _topics_card_fields(topics)
    words = len(summary.split())
    card = {
        "title": title,
        "summary": summary or "Описание отсутствует в метаданных источника.",
        "source": source or "Источник не указан",
        "via": via,
        "date": published,
        "url": canonical,
        "doi": doi,
        "reading_time": max(2, math.ceil(words / 180)) if words else 2,
    }
    card.update(style)
    if relevance:
        card["tft_rank"], card["tft_note"] = relevance
    card["id"] = item_key(card)
    return card


def _get_text(url: str, timeout: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/atom+xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_crossref(
    today: date | None,
    timeout: int,
    per_query: int,
    queries: tuple[str, ...] | None = None,
) -> list[dict]:
    """Публикации из Crossref (только journal-article с полной датой)."""
    start, end = sliding_period(today)
    cards: list[dict] = []
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()

    for query in queries or CROSSREF_QUERIES:
        params = {
            "query.bibliographic": query,
            "filter": (
                f"type:journal-article,from-pub-date:{start.isoformat()},"
                f"until-pub-date:{end.isoformat()}"
            ),
            "rows": per_query,
            "select": (
                "DOI,title,abstract,published,published-online,published-print,"
                "container-title,publisher,URL,type"
            ),
        }
        if mailto:
            params["mailto"] = mailto
        payload = json.loads(
            _get_text("https://api.crossref.org/works?" + urlencode(params), timeout)
        )
        for raw in payload.get("message", {}).get("items", []):
            published = _crossref_date(raw)
            if not published:
                continue
            title = (raw.get("title") or [""])[0]
            source = (raw.get("container-title") or [""])[0] or raw.get("publisher", "")
            card = _make_card(
                title=title,
                summary=raw.get("abstract", ""),
                source=source,
                via="Crossref",
                published=published,
                url=raw.get("URL", ""),
                doi=raw.get("DOI", ""),
            )
            if card:
                cards.append(card)
    return cards


def _openalex_abstract(inverted: dict) -> str:
    if not isinstance(inverted, dict):
        return ""
    pairs = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            try:
                pairs.append((int(position), word))
            except (TypeError, ValueError):
                continue
    pairs.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in pairs)


def fetch_openalex(
    today: date | None,
    timeout: int,
    per_query: int,
    queries: tuple[str, ...] | None = None,
) -> list[dict]:
    """Публикации из OpenAlex (статьи с датой внутри скользящего окна)."""
    start, end = sliding_period(today)
    cards: list[dict] = []
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()

    for query in queries or OPENALEX_QUERIES:
        params = {
            "filter": (
                f"from_publication_date:{start.isoformat()},"
                f"to_publication_date:{end.isoformat()},type:article"
            ),
            "search": query,
            "per-page": per_query,
            "select": (
                "id,doi,display_name,publication_date,abstract_inverted_index,"
                "primary_location,type"
            ),
        }
        if api_key:
            params["api_key"] = api_key
        payload = json.loads(
            _get_text("https://api.openalex.org/works?" + urlencode(params), timeout)
        )
        for raw in payload.get("results", []):
            published = raw.get("publication_date") or ""
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
                continue
            location = raw.get("primary_location") or {}
            source_info = location.get("source") or {}
            card = _make_card(
                title=raw.get("display_name", ""),
                summary=_openalex_abstract(raw.get("abstract_inverted_index")),
                source=source_info.get("display_name", "") or "OpenAlex",
                via="OpenAlex",
                published=published,
                url=raw.get("id", ""),
                doi=raw.get("doi", ""),
            )
            if card:
                cards.append(card)
    return cards


def fetch_arxiv(
    today: date | None,
    timeout: int,
    per_query: int,
    queries: tuple[str, ...] | None = None,
) -> list[dict]:
    """Свежие препринты arXiv по темам выпуска."""
    start, end = sliding_period(today)
    active_queries = queries or ARXIV_QUERIES
    search_query = " OR ".join(f'all:"{query}"' for query in active_queries)
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max(10, per_query * 3),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    text = _get_text(
        "https://export.arxiv.org/api/query?" + urlencode(params), timeout
    )
    root = ET.fromstring(text)
    cards: list[dict] = []

    for entry in root.findall("atom:entry", ATOM_NS):
        published = (entry.findtext("atom:published", default="", namespaces=ATOM_NS) or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
            continue
        if not (start.isoformat() <= published <= end.isoformat()):
            continue
        title = entry.findtext("atom:title", default="", namespaces=ATOM_NS)
        summary = entry.findtext("atom:summary", default="", namespaces=ATOM_NS)
        url = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        card = _make_card(
            title=title,
            summary=summary,
            source="arXiv",
            via="arXiv API · препринт",
            published=published,
            url=url,
        )
        if card:
            cards.append(card)
    return cards


def _vacancy_card(
    *,
    title: str,
    description: str,
    company: str,
    published: str,
    url: str,
    via: str,
) -> dict | None:
    """Строит карточку вакансии только из проверенных метаданных API."""
    matched = is_relevant_vacancy(title, description)
    if not matched:
        return None
    topics = ["jobs"] + [slug for slug in RESEARCH_SLUGS if slug in matched]
    card = _make_card(
        title=title,
        summary=description,
        source=company or "Работодатель не указан",
        via=via,
        published=published,
        url=url,
        topics=topics,
    )
    if card:
        card["is_vacancy"] = True
    return card


def fetch_remotive_jobs(today, timeout, per_query, queries=None) -> list[dict]:
    """Вакансии Remotive: открытый keyless API с датой публикации."""
    start, end = sliding_period(today)
    cards: list[dict] = []
    for query in queries or JOB_QUERIES:
        params = {"search": query, "limit": max(10, per_query)}
        payload = json.loads(
            _get_text(
                "https://remotive.com/api/remote-jobs?" + urlencode(params), timeout
            )
        )
        for raw in payload.get("jobs", []):
            published = str(raw.get("publication_date", ""))[:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
                continue
            if not (start.isoformat() <= published <= end.isoformat()):
                continue
            card = _vacancy_card(
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                company=raw.get("company_name", ""),
                published=published,
                url=raw.get("url", ""),
                via="Remotive",
            )
            if card:
                cards.append(card)
    return cards


def fetch_arbeitnow_jobs(today, timeout, per_query, queries=None) -> list[dict]:
    """Вакансии Arbeitnow: открытый keyless API с UNIX-датой создания."""
    start, end = sliding_period(today)
    payload = json.loads(
        _get_text("https://www.arbeitnow.com/api/job-board-api", timeout)
    )
    cards: list[dict] = []
    for raw in payload.get("data", []):
        try:
            created = int(raw.get("created_at", 0))
            published = datetime.fromtimestamp(created, timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            continue
        if not (start.isoformat() <= published <= end.isoformat()):
            continue
        card = _vacancy_card(
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            company=raw.get("company_name", ""),
            published=published,
            url=raw.get("url", ""),
            via="Arbeitnow",
        )
        if card:
            cards.append(card)
    return cards


FETCHERS = (
    ("Crossref", fetch_crossref),
    ("OpenAlex", fetch_openalex),
    ("arXiv", fetch_arxiv),
)

JOB_FETCHERS = (
    ("Remotive", fetch_remotive_jobs),
    ("Arbeitnow", fetch_arbeitnow_jobs),
)


def collect_candidates(
    topic: str = "all",
    today: date | None = None,
    timeout: int | None = None,
    per_query: int | None = None,
    fetchers=None,
    fallback: bool = False,
) -> dict:
    """Опрашивает источники для активного тега и собирает уникальные карточки.

    Для вакансий используются отдельные keyless API; для остальных тегов —
    те же три источника публикаций, но с запросами только по выбранной теме.
    Ошибка одного источника не отменяет результаты остальных.
    """
    timeout = timeout or _env_int("NEWS_SOURCE_TIMEOUT", 8)
    per_query = per_query or _env_int("NEWS_SOURCE_PER_QUERY", 20)
    if fetchers is not None:
        active = fetchers
    elif topic == "jobs":
        active = JOB_FETCHERS
    else:
        active = FETCHERS
    queries = queries_for_topic(topic, fallback=fallback)
    candidates: list[dict] = []
    errors: list[str] = []
    seen_keys: set[str] = set()

    with ThreadPoolExecutor(max_workers=max(1, len(active))) as pool:
        futures = {
            pool.submit(_call_fetcher, fetcher, today, timeout, per_query, queries): name
            for name, fetcher in active
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                items = future.result()
            except Exception as exc:  # сеть, таймаут, формат ответа
                errors.append(f"{name}: {type(exc).__name__}")
                continue
            for item in items:
                key = item_key(item)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                item["id"] = key
                candidates.append(item)

    candidates.sort(key=lambda item: (item["date"], item["title"]), reverse=True)
    return {
        "items": candidates,
        "errors": errors,
        "sources": [name for name, _ in active],
        "topic": topic,
        "fallback": fallback,
    }


def select_items(
    candidates: list[dict],
    seen_keys,
    target_count: int,
    start: date,
    end: date,
    topic: str = "all",
    diversify: bool | None = None,
    rng=None,
) -> list[dict]:
    """Отбирает новые карточки активного тега внутри периода, без повторов.

    Материалы проверяются по содержанию (`matches_topic`), а не только по
    совпадению слова в заголовке. Для «Все» включено случайное чередование
    тем; для TFT сохраняется приоритет релевантности 0 → 1 → 2.
    """
    seen = set(seen_keys or [])
    unique: list[dict] = []
    keys: set[str] = set()

    # Сначала собираем уникальные репрезентативы в исходном порядке — так
    # один и тот же URL не меняет «хозяина» в зависимости от даты дубля.
    for item in candidates:
        published = item.get("date", "")
        try:
            published_date = date.fromisoformat(published)
        except (TypeError, ValueError):
            continue
        if not (start <= published_date <= end):
            continue
        if not matches_topic(item, topic):
            continue

        key = item_key(item)
        if key in seen or key in keys:
            continue
        keys.add(key)
        unique.append(item)

    unique.sort(
        key=lambda value: (value.get("date", ""), value.get("title", "")), reverse=True
    )
    if topic != "tft":
        unique.sort(key=lambda value: value.get("tft_rank", 3))

    if diversify is None:
        diversify = topic == "all"
    if diversify:
        return _diversify(unique, target_count, rng or random.Random())
    return unique[:target_count]
