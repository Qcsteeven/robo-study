"""Связи профилей и дисциплин с преподавателями из справочника ИРНИТУ."""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from .http import Fetched, SiteClient, SiteError

_STRUCTURE_PATH = "/struktura/"
_REFERENCE_DIRECTORIES = (
    "/struktura/iit/sotrudniki/po_podrazdeleniyam",
    "/struktura/iit/sotrudniki/vnesh_prepod",
    "/struktura/ieup_iif/sotrudniki/pps_if",
)
_ISTU = "https://www.istu.edu"
_PERSON_HOSTS = {"www.istu.edu", "istu.edu"}
_SCHEDULE_SEARCH_PATH = "/raspisanie/poisk"
TEACHER_SCHEDULE_SEARCH = _ISTU + _SCHEDULE_SEARCH_PATH
_SCHEDULE_TEACHER = re.compile(r"/raspisanie/prepodavatel/[0-9]+/?")
_VERIFIED_PROFILES = Path(__file__).resolve().parents[2] / "config" / "teacher_profiles.json"
_PERSON_PATH = re.compile(r"/person/[A-Za-z0-9_-]+/?")
_UNIT_PATH = re.compile(r"/struktura/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*/?")
_ACADEMIC_UNIT = re.compile(r"^(?:институт|кафедра|факультет|филиал)\b|\b(?:колледж|техникум)\b", re.I)
_STAFF_DIRECTORY = re.compile(r"/(?:sotrudniki|personalii|prepodavateli|pps(?:_[a-z0-9_-]+)?)(?:/|$)")
_STAFF_LABEL = re.compile(
    r"^(?:сотрудники|преподаватели|персоналии|профессорско-преподавательский состав|"
    r"педагогический состав|кадровый состав|список преподавателей|список .* преподавателей|по подразделениям|ппс)\b",
    re.I,
)
_LEVEL_CODES = {"specialist": 1, "bachelor": 2, "master": 3, "postgraduate": 5}
_CODE = re.compile(r"^(\d+(?:\.\d+){1,2})\.?(?:\s+|$)")
_DASHES = str.maketrans({"\u00ad": None, "\u2010": "-", "\u2011": "-", "\u2013": "-", "\u2014": "-"})
_TEACHER_LIST = re.compile(r"^(.+?)\s*\(([^()]*)\)\s*$")
_INITIALLED_NAME = re.compile(r"(?:[^\W\d_]+(?:[’'-][^\W\d_]+)*\s+)+[^\W\d_]\.(?:\s*[^\W\d_]\.){0,2}\.?")


_HEADINGS = ("h2", "h3", "h4", "h5", "h6")
_TEACHING_LABEL = re.compile(r"^\s*(?:основные\s+)?(?:преподаваемые|читаемые)\s+дисциплины\s*:\s*", re.I)


@dataclass(frozen=True)
class PersonProfile:
    name: str
    url: str


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


def teacher_name_key(name: str) -> str:
    parts = name.split()
    if len(parts) > 1 and not _INITIALLED_NAME.fullmatch(name):
        name = parts[0] + "".join(part[0] for part in parts[1:])
    return teacher_discipline_key(name).replace(" ", "").replace(".", "")


def _person_path(url: str) -> bool:
    return bool(_PERSON_PATH.fullmatch(urlsplit(url).path))


def _istu_path(url: str | None) -> str:
    if not isinstance(url, str):
        return ""
    try:
        target = urlsplit(url)
    except ValueError:
        return ""
    return target.path if target.scheme == "https" and target.netloc.lower() in _PERSON_HOSTS else ""


def teacher_profile_url(url: str | None) -> str | None:
    """Возвращает канонический адрес персоналии ИРНИТУ."""
    path = _istu_path(url)
    return _ISTU + path.rstrip("/") if _PERSON_PATH.fullmatch(path) else None


def teacher_schedule_url(url: str | None) -> str | None:
    """Возвращает канонический адрес личного расписания ИРНИТУ."""
    path = _istu_path(url)
    return _ISTU + path.rstrip("/") + "/" if _SCHEDULE_TEACHER.fullmatch(path) else None


def teacher_link(url: str | None) -> str:
    return teacher_profile_url(url) or teacher_schedule_url(url) or TEACHER_SCHEDULE_SEARCH


@cache
def _verified_teacher_data() -> dict:
    try:
        return json.loads(_VERIFIED_PROFILES.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def verified_person_links() -> tuple[tuple[str, str], ...]:
    rows = _verified_teacher_data().get("profiles", {})
    return tuple((name, url) for name, urls in rows.items() for url in urls)


def _named_links(page: Fetched) -> Iterable[tuple[str, str]]:
    for link in BeautifulSoup(page.text, "lxml").find_all("a", href=True):
        name = " ".join(link.get_text(" ", strip=True).split())
        yield name, urljoin(page.url, link["href"])


def parse_person_profiles(
    pages: Iterable[Fetched],
    extra_links: Iterable[tuple[str, str]] = (),
) -> dict[str, tuple[PersonProfile, ...]]:
    """Сохраняет все персональные карточки с совпадающей фамилией и инициалами и порядок их обнаружения."""
    links = [link for page in pages for link in _named_links(page)]
    links.extend(extra_links)

    candidates: dict[str, dict[str, PersonProfile]] = {}
    for name, href in links:
        url = teacher_profile_url(href)
        if url and len(name.split()) >= 2:
            candidates.setdefault(teacher_name_key(name), {}).setdefault(url, PersonProfile(name, url))
    return {key: tuple(by_url.values()) for key, by_url in candidates.items()}


def _structure_links(page: Fetched, *, index: bool = False) -> list[str]:
    """Находит учебные подразделения или опубликованные списки их сотрудников."""
    soup = BeautifulSoup(page.text, "lxml")
    selector = "main.page-content .multilist-item > a[href]" if index else "a[href]"
    origin = urlsplit(page.url)
    unit = origin.path.split("/")[2:3]
    urls = []
    for link in soup.select(selector):
        name = teacher_discipline_key(link.get_text(" ", strip=True))
        target = urlsplit(urljoin(page.url, link["href"]))
        if (
            target.scheme != "https" or target.netloc.lower() != origin.netloc.lower()
            or not _UNIT_PATH.fullmatch(target.path)
        ):
            continue
        if index:
            matches = _ACADEMIC_UNIT.search(name)
        else:
            same_unit = target.path.split("/")[2:3] == unit
            matches = same_unit and (_STAFF_DIRECTORY.search(target.path) or _STAFF_LABEL.match(name))
        if matches:
            urls.append(f"{target.scheme}://{target.netloc}{target.path.rstrip('/')}")
    return list(dict.fromkeys(urls))


def load_person_profiles(site: SiteClient) -> dict[str, tuple[PersonProfile, ...]]:
    """Собирает персоналии из структуры ИРНИТУ и добавляет проверенные резервные адреса."""
    verified = verified_person_links()
    pages = []
    try:
        index = site.get(urljoin(site.s.base_url, _STRUCTURE_PATH), max_bytes=2 * 2**20)
    except SiteError:
        units = []
    else:
        pages.append(index)
        units = _structure_links(index, index=True)

    visited = set()

    def fetch(url):
        try:
            return site.get(url, max_bytes=2 * 2**20)
        except SiteError:
            return None

    def fetch_new(executor, urls):
        new = list(dict.fromkeys(url for url in urls if url not in visited))
        visited.update(new)
        return [page for page in executor.map(fetch, new) if page is not None]

    reference = [urljoin(site.s.base_url, path) for path in _REFERENCE_DIRECTORIES]
    with ThreadPoolExecutor(max_workers=6) as executor:
        unit_pages = fetch_new(executor, units)
        staff_pages = fetch_new(executor, reference + [url for page in unit_pages for url in _structure_links(page)])
        sections = fetch_new(executor, [url for page in staff_pages for url in _structure_links(page)])
    pages.extend(unit_pages + staff_pages + sections)
    return parse_person_profiles(pages, verified)


def _parse_person_disciplines(html: str) -> frozenset[str]:
    soup = BeautifulSoup(html, "lxml")
    courses = []
    for heading in soup.find_all(_HEADINGS):
        if teacher_discipline_key(heading.get_text(" ", strip=True)) not in {"дисциплины", "преподаваемые дисциплины"}:
            continue
        for block in heading.find_next_siblings():
            if block.name in _HEADINGS:
                break
            lines = block.find_all("li") or [block]
            courses.extend(line.get_text(" ", strip=True) for line in lines)

    for label in soup.find_all(string=_TEACHING_LABEL):
        container = label.find_parent(("p", "li", "td", "dt", "div"))
        line = container.get_text(" ", strip=True) if container else str(label)
        value = _TEACHING_LABEL.sub("", line, count=1)
        if value == line:
            continue
        if not value and container and container.name == "dt":
            field = container.find_next_sibling("dd")
            value = field.get_text(" ", strip=True) if field else ""
        quoted = re.findall(r"«([^»]+)»", value)
        courses.append(value)
        courses.extend(quoted or re.split(r"[;,]", value))
    return frozenset(teacher_discipline_key(name.strip(' .«»"')) for name in courses if name.strip())


def load_person_disciplines(site: SiteClient, url: str) -> frozenset[str]:
    try:
        card = site.get(url, max_bytes=2 * 2**20)
        if teacher_profile_url(card.url):
            return _parse_person_disciplines(card.text)
    except SiteError:
        pass
    return frozenset(
        teacher_discipline_key(name) for name in _verified_teacher_data().get("disciplines", {}).get(url, ())
    )


def _schedule_search_form(site: SiteClient) -> tuple[str, str, str, dict[str, str]] | None:
    try:
        page = site.get(urljoin(site.s.base_url, _SCHEDULE_SEARCH_PATH), max_bytes=2 * 2**20)
    except SiteError:
        return None
    soup = BeautifulSoup(page.text, "lxml")
    for form in soup.select("main form"):
        field = form.select_one('input[name]:is([type="text"], [type="search"], :not([type]))')
        action = urljoin(page.url, form.get("action") or page.url)
        if not field or not _istu_path(action).startswith("/raspisanie/"):
            continue
        data = {item["name"]: item.get("value", "") for item in form.select('input[type="hidden"][name]')}
        return action, form.get("method", "get").casefold(), field["name"], data
    return None


def load_teacher_schedules(
    site: SiteClient, names: Iterable[str], previous: dict[str, str] | None = None,
) -> dict[str, str]:
    """Ищет персональные страницы расписания для преподавателей без карточек."""
    urls = {
        teacher_name_key(name): url for name, href in _verified_teacher_data().get("schedules", {}).items()
        if (url := teacher_schedule_url(href))
    }
    urls.update(previous or {})
    missing = {name.split()[0] for name in names if name.split() and teacher_name_key(name) not in urls}
    if not missing or not (form := _schedule_search_form(site)):
        return urls
    action, method, field, data = form

    def search(surname: str) -> list[tuple[str, str]]:
        values = dict(data)
        values[field] = surname
        try:
            if method == "post":
                page = site.post_form(action, values, max_bytes=2 * 2**20)
            else:
                parts = urlsplit(action)
                query = urlencode({**dict(parse_qsl(parts.query)), **values})
                page = site.get(urlunsplit(parts._replace(query=query)), max_bytes=2 * 2**20)
        except SiteError:
            return []
        return [(name, url) for name, href in _named_links(page) if (url := teacher_schedule_url(href))]

    with ThreadPoolExecutor(max_workers=6) as executor:
        for links in executor.map(search, sorted(missing)):
            for name, url in links:
                urls.setdefault(teacher_name_key(name), url)
    return urls


def resolve_teacher_profiles(
    directory: dict[str, list[dict[str, str]]],
    profiles: dict[str, tuple[PersonProfile, ...]],
    *,
    disciplines_for: Callable[[str], frozenset[str]] | None = None,
    schedules: dict[str, str] | None = None,
    previous: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Разрешает совпадения по дисциплине, затем по порядку обнаружения карточек."""
    resolved = {}
    for discipline, teachers in directory.items():
        saved = {
            teacher_name_key(teacher["name"]): teacher["url"]
            for teacher in (previous or {}).get(discipline, []) if teacher.get("url")
        }
        course = teacher_discipline_key(discipline)
        rows = {}
        for teacher in teachers:
            name = teacher_name_key(teacher["name"])
            original = teacher.get("url")
            candidates = list(profiles.get(name, ()))
            if (direct := teacher_profile_url(original)) and all(item.url != direct for item in candidates):
                candidates.append(PersonProfile(teacher["name"], direct))
            if candidates:
                matched = [
                    item for item in candidates if course in disciplines_for(item.url)
                ] if len(candidates) > 1 and disciplines_for else []
                selected = matched or candidates[:1]
                for item in selected:
                    label = item.name if len(selected) > 1 else teacher["name"]
                    rows.setdefault((name, item.url), dict(teacher, name=label, url=item.url))
            else:
                url = (teacher_profile_url(saved.get(name)) or teacher_schedule_url(original)
                       or teacher_schedule_url(saved.get(name)) or (schedules or {}).get(name)
                       or TEACHER_SCHEDULE_SEARCH)
                rows.setdefault((name, url), dict(teacher, url=url))
        resolved[discipline] = list(rows.values())
    return resolved


def _teacher_urls(item: Tag, page_url: str) -> dict[str, str]:
    urls = {}
    host = urlsplit(page_url).hostname
    for link in item.find_all("a", href=True):
        url = urljoin(page_url, link["href"])
        target = urlsplit(url)
        if (target.scheme == "https" and target.hostname == host and target.username is None
                and (_person_path(url) or target.path.startswith("/sveden/employees/"))):
            name = teacher_discipline_key(link.get_text(" ", strip=True))
            if _person_path(url) or name not in urls:
                urls[name] = url
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
            urls = _teacher_urls(node, page_url)
            discipline = matches.setdefault(teacher_discipline_key(assignment[1]), {})
            for name in names:
                url = urls.get(teacher_discipline_key(name))
                if name not in discipline or (url and not _person_path(discipline[name])):
                    discipline[name] = url or page_url
    return {key: [{"name": name, "url": url} for name, url in candidates.items()]
            for key, candidates in matches.items()}
