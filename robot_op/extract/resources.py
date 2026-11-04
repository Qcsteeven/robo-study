"""Сохранение внешних ресурсов из полного текста PDF и URI-аннотаций."""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

URL = re.compile(r"(?:https?://|www\.)[^\s<>\"«»]+", re.I)
HEADING = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2})*\.?\s+(?:Методическ|Ресурс|Основн|Дополнительн|Литератур|Перечень|Содержан|Структур|Фонд|Оценочн|Материально|Информационн|Профессиональн|Требован|Учебно-методическ|Форм|Объем|Объём|Планируем|Место)\w*", re.I)
ENTRY = re.compile(r"^\s*\d{1,3}[.)]\s+")


def safe_url(value: str) -> str:
    value = html.unescape(value or "").strip().rstrip(".,;:")
    for left, right in (("(", ")"), ("[", "]")):
        while value.endswith(right) and value.count(right) > value.count(left):
            value = value[:-1]
    if value.startswith("www."):
        value = "https://" + value
    if re.search(r"\s|[\x00-\x1f\x7f]", value):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def _label(text: str) -> str:
    text = URL.sub("", text)
    text = re.sub(r"^\s*\d{1,3}[.)](?:\s+|$)", "", text.strip())
    text = re.sub(r"(?:[—–-]\s*)?\bссылка\s*[.:]?\s*$", "", text, flags=re.I)
    value = re.sub(r"\s+", " ", text).strip(" .:—–-")[:500]
    return "" if value.isdigit() else value


def repair_url_wraps(text: str) -> str:
    """Соединить перенос адреса; следующий самостоятельный URL не присоединяется."""
    pattern = re.compile("(" + URL.pattern + r")([ \t\r\n]+)(?=([A-Za-z0-9%_~./?#=&+;:@!$,'()*-]+)(?:\s|$))", re.I)
    def join(match):
        url, space, fragment = match[1], match[2], match[3]
        if not URL.match(fragment) and re.search(r"[A-Za-z%]", fragment):
            if url.endswith(("-", "/", "&", "=", "?", "#", "%")) or re.search(r"[/#?%=&-]", fragment):
                return url
        return url + space
    previous = None
    while previous != text:
        previous, text = text, pattern.sub(join, text)
    return text


def _lines(page: str) -> list[str]:
    return [line.strip() for line in repair_url_wraps(page).splitlines()]


def _heading(line: str) -> bool:
    # «1. Ресурсы по курсу ...» — пункт перечня, не новый раздел.
    return bool(HEADING.match(line) and not ENTRY.match(line))


def collect(text: str, hyperlinks: list[dict] | None = None) -> list[dict]:
    result = []
    current = "Документ"
    page_starts = {}
    by_url = {}

    def add(url, title, section, page):
        title = _label(title) or (urlsplit(url).netloc if url else "Ресурс без адреса")
        origin = {"section": section.rstrip(" :"), "page": page}
        if url and url in by_url:
            item = by_url[url]
        else:
            # URI-аннотация раскрывает адрес за текстом «ссылка».
            item = next((r for r in result if not r["url"] and r["title"].casefold() == title.casefold()
                         and any(o["page"] == page for o in r["origins"])), None)
            if item is None:
                item = {"url": url, "title": title, "origins": []}
                result.append(item)
            elif url:
                item["url"] = url
            if url:
                by_url[url] = item
        if origin not in item["origins"]:
            item["origins"].append(origin)

    for page, source in enumerate(text.split("\f"), 1):
        page_starts[page] = current
        buffer = []
        for line in _lines(source):
            if not line or re.fullmatch(r"\d{1,3}", line):
                continue
            if _heading(line):
                current = URL.sub("", line).rstrip(" :")
                buffer = []
                if not URL.search(line):
                    continue
            if ENTRY.match(line):
                buffer = []
            buffer.append(line)
            urls = [(m.group(), safe_url(m.group())) for m in URL.finditer(line)]
            for raw, url in urls:
                if url:
                    add(url, " ".join(buffer), current, page)
            if urls:
                buffer = []
            elif re.search(r"\bссылка[.:]?\s*$", line, re.I):
                add("", " ".join(buffer), current, page)
                buffer = []
    for link in hyperlinks or []:
        url = safe_url(link.get("url") or "")
        if not url:
            continue
        page = link.get("page", 1)
        section = page_starts.get(page, "Документ")
        buffer = []
        for line in _lines(link.get("context") or ""):
            if _heading(line):
                section = line.rstrip(" :")
                buffer = []
            else:
                if ENTRY.match(line):
                    buffer = []
                buffer.append(line)
        title = " ".join(buffer) if buffer else link.get("text") or url
        add(url, title, section, page)
    return result
