"""Разбор страниц раздела «Сведения об образовательной организации. Образование» сайта ИРНИТУ.

Разбор не привязан к жёсткой вёрстке: таблица уровней/годов ищется по названиям уровней в первой
колонке, блок ОП — по таблице со строками «Уровень», «Форма обучения», «Год» (ТЗ, рисунки 1–3),
тип документа — по подписи строки. Ссылки на подписи (sig) и неподдерживаемые типы отбрасываются.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..catalog import LEVELS, level_key

CODE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{2})\b\s*[«\"]?([А-ЯЁA-Z][^\n«»\"]{3,200})?")
YEAR_RE = re.compile(r"\b(20\d{2})\b")


@dataclass
class LevelYears:
    level: str
    title: str
    years: dict[int, str] = field(default_factory=dict)   # год → адрес страницы перечня ОП


@dataclass
class DocLink:
    doc_type: str
    title: str
    url: str


@dataclass
class ProgramBlock:
    key: str
    title: str
    profile: str
    form: str
    level_title: str
    year: int | None
    code: str
    direction: str
    docs: list[DocLink] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)      # подписи строк, которые робот не обрабатывает

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "profile": self.profile, "form": self.form,
                "code": self.code, "direction": self.direction, "year": self.year,
                "documents": len(self.docs)}


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True) if el is not None else "").strip()


# ---------------------------------------------------------------- уровни образования и годы набора

def parse_education(html: str, page_url: str) -> list[LevelYears]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, LevelYears] = {}
    for table in soup.find_all("table"):
        caption = _caption_before(table)
        if re.search(r"филиал", caption, re.I):
            continue   # таблицы филиалов не входят в перечень (ТЗ, рисунок 1)
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            key = level_key(_text(cells[0]))
            if not key:
                continue
            item = found.setdefault(key, LevelYears(key, LEVELS[key]))
            for a in tr.find_all("a", href=True):
                m = YEAR_RE.search(_text(a))
                if m:
                    item.years.setdefault(int(m.group(1)), urljoin(page_url, a["href"]))
    order = list(LEVELS)
    return sorted((v for v in found.values() if v.years), key=lambda v: order.index(v.level))


def _caption_before(table: Tag) -> str:
    cap = table.find("caption")
    if cap:
        return _text(cap)
    prev = table.find_previous(string=lambda s: s and s.strip() and s.find_parent("table") is None)
    return prev.strip() if prev else ""


# ---------------------------------------------------------------- перечень ОП и документы

# подпись строки блока ОП → тип документа; порядок важен: сначала исключения
ROW_RULES: list[tuple[str, str | None]] = [
    (r"адаптир|\bаоп\b|аннотац|воспитан|стандарт|фгос|календарный план", None),
    (r"календарн\w* учебн\w* график|\bкуг\b", "kug"),
    (r"учебн\w* план|^уп$", "up"),
    (r"фос\s*гиа|фонд оценочных средств\s*гиа|фонд оценочных средств.*итогов", "fos_gia"),
    (r"программа\s*гиа|государственн\w* итогов\w* аттестац|^гиа$", "gia"),
    (r"\bрпд\b|рабоч\w* программ\w* дисциплин|^дисциплин", "rpd"),
    (r"практик", "practice"),
    (r"\bооп\b|\bопоп\b|образовательн\w* программ", "oop"),
]
META_LABELS = {"уровень", "форма обучения", "год", "код", "направление", "специальность", "профиль",
               "направление подготовки", "наименование"}


def row_doc_type(label: str) -> str | None | bool:
    """Тип документа по подписи строки; None — тип не обрабатывается; False — строка не о документах."""
    t = label.strip().lower()
    if not t or t in META_LABELS:
        return False
    for pattern, doc_type in ROW_RULES:
        if re.search(pattern, t):
            return doc_type
    return None


def _is_doc_href(href: str, text: str) -> bool:
    h = href.strip().lower()
    if not h or h.startswith(("#", "javascript:", "mailto:")):
        return False
    if h.split("?")[0].endswith(".sig") or text.strip().lower() == "sig":
        return False
    return True


def _row_links(tr: Tag, soup: BeautifulSoup, page_url: str) -> list[tuple[str, str]]:
    cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
    holder = cells[-1] if len(cells) > 1 else tr
    links: list[tuple[str, str]] = []
    for a in holder.find_all("a"):
        href, text = a.get("href", ""), _text(a)
        target = a.get("data-target") or a.get("data-bs-target") or (href if href.startswith("#") and len(href) > 1 else "")
        if target:
            # список документов в модальном окне (ТЗ, рисунок 3 — список РПД)
            modal = soup.find(id=target.lstrip("#"))
            if modal is not None:
                for ma in modal.find_all("a", href=True):
                    if _is_doc_href(ma["href"], _text(ma)):
                        links.append((_text(ma), urljoin(page_url, ma["href"])))
            continue
        if _is_doc_href(href, text):
            links.append((text, urljoin(page_url, href)))
    seen, unique = set(), []
    for title, url in links:
        if url not in seen:
            seen.add(url)
            unique.append((title, url))
    return unique


def _block_title(table: Tag) -> str:
    node: Tag | None = table
    for _ in range(5):
        if node is None:
            break
        for sib in node.find_previous_siblings():
            if not isinstance(sib, Tag) or sib.name in ("script", "style", "table"):
                continue
            t = _text(sib)
            if t:
                return t
        node = node.parent
    return ""


def _split_title(title: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return title.strip(), ""


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r",
                      "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
_FORM_SLUG = {"очная": "och", "заочная": "zaoch", "очно-заочная": "ochzaoch"}


def program_key(code: str, profile: str, year: int | None, form: str) -> str:
    words = [w for w in re.findall(r"[а-яёa-z]+", profile.lower()) if len(w) > 2 or w in ("ит",)]
    abbr = "".join(_TRANSLIT.get(w[0], w[0]) for w in words)[:12] or "op"
    form_slug = _FORM_SLUG.get(form.strip().lower(), re.sub(r"\W", "", "".join(_TRANSLIT.get(c, c) for c in form.lower()))[:8] or "na")
    return "-".join(p for p in (code or "op", abbr, str(year or ""), form_slug) if p)


def parse_op_list(html: str, page_url: str, level_title: str = "", year: int | None = None) -> list[ProgramBlock]:
    soup = BeautifulSoup(html, "lxml")
    blocks: list[ProgramBlock] = []
    used: set[str] = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        labels = {}
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) >= 2:
                labels[_text(cells[0]).lower()] = tr
        if "уровень" not in labels or not ({"форма обучения", "год"} & set(labels)):
            continue
        title = _block_title(table)
        profile, form_in_title = _split_title(title)

        def value(label: str) -> str:
            tr = labels.get(label)
            return _text(tr.find_all(["td", "th"])[-1]) if tr is not None else ""

        form = value("форма обучения") or form_in_title
        y = YEAR_RE.search(value("год"))
        code, direction = _code_direction(table, labels)
        if labels.get("профиль") is not None and value("профиль"):
            profile = value("профиль")
        block = ProgramBlock(
            key="", title=title, profile=profile, form=form.capitalize(),
            level_title=(value("уровень") or level_title).capitalize(),
            year=int(y.group(1)) if y else year, code=code, direction=direction)
        for label, tr in labels.items():
            doc_type = row_doc_type(label)
            if doc_type is False:
                continue
            if doc_type is None:
                block.excluded.append(label)
                continue
            for text, url in _row_links(tr, soup, page_url):
                block.docs.append(DocLink(doc_type, text or label, url))
        key = program_key(block.code, block.profile, block.year, block.form)
        if key in used:
            key += "-" + hashlib.sha1(title.encode()).hexdigest()[:4]
        used.add(key)
        block.key = key
        blocks.append(block)
    return blocks


def _code_direction(table: Tag, labels: dict) -> tuple[str, str]:
    for label in ("код", "направление подготовки", "направление", "специальность"):
        tr = labels.get(label)
        if tr is not None:
            m = CODE_RE.search(_text(tr))
            if m:
                return m.group(1), (m.group(2) or "").strip(" .,")
    # ближайший предшествующий заголовок с кодом направления
    prev = table.find_previous(string=CODE_RE)
    if prev:
        m = CODE_RE.search(prev)
        direction = (m.group(2) or "").strip(" .,")
        if not direction:
            parent_text = _text(prev.parent)
            m2 = CODE_RE.search(parent_text)
            direction = (m2.group(2) or "").strip(" .,") if m2 else ""
        return m.group(1), direction
    return "", ""
