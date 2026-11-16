"""Связи профилей и дисциплин с преподавателями из справочника ИРНИТУ."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .http import Fetched, SiteClient

_LEVEL_CODES = {"specialist": 1, "bachelor": 2, "master": 3, "postgraduate": 5}
_CODE = re.compile(r"^(\d+(?:\.\d+){1,2})\.?(?:\s+|$)")
_DASHES = str.maketrans({"\u00ad": None, "\u2010": "-", "\u2011": "-", "\u2013": "-", "\u2014": "-"})
_TEACHER_LIST = re.compile(r"^(.+?)\s*\(([^()]*)\)\s*$")
_INITIALLED_NAME = re.compile(r"(?:[^\W\d_]+(?:[’'-][^\W\d_]+)*\s+)+[^\W\d_]\.(?:\s*[^\W\d_]\.){0,2}\.?")


def teacher_discipline_key(name: str) -> str:
    """Нормализует регистр, пробелы, ё и типографские дефисы для точного сравнения."""
    text = unicodedata.normalize("NFKC", name).casefold().replace("ё", "е").translate(_DASHES)
    text = re.sub(r"\s*-\s*", "-", text)
    return " ".join(text.split()).rstrip(".").strip()


def load_level_directory(site: SiteClient, level: str) -> Fetched | None:
    """Загружает перечень дисциплин и преподавателей для одного уровня."""
    level_code = _LEVEL_CODES.get(level)
    if level_code is None:
        return None
    url = urljoin(site.s.base_url, f"/sveden/education/direction_prep?level={level_code}")
    return site.get(url, max_bytes=8 * 2**20)


def _employee_urls(item: Tag, page_url: str) -> dict[str, str]:
    urls = {}
    host = urlsplit(page_url).hostname
    for link in item.find_all("a", href=True):
        url = urljoin(page_url, link["href"])
        target = urlsplit(url)
        if (target.scheme == "https" and target.hostname == host and target.username is None
                and target.path.startswith("/sveden/employees/")):
            urls[teacher_discipline_key(link.get_text(" ", strip=True))] = url
    return urls


def match_program_teachers(html: str, page_url: str, *, code: str, profile: str) -> dict[str, list[dict[str, str]]]:
    """Ищет кандидатов по коду направления, названию профиля и дисциплине.

    Перечень по профилю объединяет преподавателей без разбивки по году набора,
    форме обучения и семестру. Несколько найденных имён сохраняются.
    """
    profile_code = _CODE.match(profile.strip())
    direction_code = code.strip()
    if profile_code:
        profile = profile.strip()[profile_code.end():]
        if not direction_code:
            direction_code = profile_code[1]
            parts = direction_code.split(".")
            if len(parts) == 3 and len(parts[0]) == 1:
                direction_code = ".".join(parts[:2])
    profile_key = teacher_discipline_key(profile)
    if not direction_code or not profile_key:
        return {}

    matches: dict[str, dict[str, str]] = {}
    soup = BeautifulSoup(html, "lxml")
    direction_matches = profile_matches = False
    for node in soup.find_all(["h1", "h2", "h3", "h4", "li"]):
        text = node.get_text(" ", strip=True)
        if node.name in ("h1", "h2", "h3"):
            section_code = _CODE.match(text) if node.name == "h3" else None
            direction_matches = bool(section_code and section_code[1] == direction_code)
            profile_matches = False
        elif node.name == "h4":
            profile_matches = direction_matches and teacher_discipline_key(text) == profile_key
        elif profile_matches:
            assignment = _TEACHER_LIST.match(text)
            if not assignment:
                continue
            names = [name.strip() for name in assignment[2].split(",")]
            if not all(_INITIALLED_NAME.fullmatch(name) for name in names):
                continue
            urls = _employee_urls(node, page_url)
            discipline = matches.setdefault(teacher_discipline_key(assignment[1]), {})
            for name in names:
                url = urls.get(teacher_discipline_key(name))
                if url or name not in discipline:
                    discipline[name] = url or page_url
    return {key: [{"name": name, "url": url} for name, url in candidates.items()]
            for key, candidates in matches.items()}
