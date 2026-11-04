"""Таблицы РПД, программ практик и ГИА с текстовым слоем (ТЗ, шаг 6).

Таблица опознаётся по шапке; таблица без шапки с тем же числом колонок сразу после опознанной — её
продолжение на следующей странице. Из таблиц сведения берутся точнее, чем из сплошного текста: текст
таблицы при извлечении перемешивается по колонкам.
"""
from __future__ import annotations

import re

from .quality import learning_results

# вид таблицы → признаки шапки (все должны встретиться в первых строках)
KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("volume", (r"вид\s+учебной\s+работы",)),
    ("plan", (r"наименование\s+раздела",)),
    ("topics", (r"\bтема\b", r"краткое\s+содержание")),
    ("self_study", (r"вид\s+срс",)),
    ("labs", (r"наименование\s+лабораторн",)),
    ("practicals", (r"тем\w*\s+практическ|практическ\w+\s+\(?семинар",)),
    ("competencies", (r"код,?\s+наименование\s+компетенц",)),
    ("indicators", (r"код\s+индикатора", r"результат")),
    ("practice_period", (r"период\s+проведения",)),
    ("stages", (r"\bэтап\b", r"содержание\s+работ")),
    ("vkr_topics", (r"тем\w*\s+(выпускн|вкр)|перечень\s+тем",)),
    # таблицы оценивания: не нужны, но не должны сойти за продолжение предыдущей таблицы
    ("other", (r"индикатор\w*\s+достижения|критери\w+\s+оценивания|^отлично|^зачтено",)),
]
COMP_CODE = re.compile(r"^((?:[А-ЯЁA-Z]{1,5})(?:\s[А-ЯЁA-Z]{1,3})?-\s?\d{1,2})\.?\s*(.*)$")


def classify(rows: list[list[str]]) -> str | None:
    """Вид таблицы по шапке — первой строке (у таблицы объёма «Всего / Семестр» — во второй)."""
    head = " ".join(rows[0]).lower().replace("ё", "е") if rows else ""
    for kind, signs in KINDS:
        if all(re.search(s, head) for s in signs):
            return kind
    return None


def group(tables: list[tuple[int, list[list[str]]]], sources: dict | None = None) -> dict[str, list[list[str]]]:
    """Опознанные таблицы с продолжениями: вид → строки, включая повторные шапки семестров.

    Продолжение — таблица без шапки сразу после опознанной: с тем же числом колонок или первая на следующей
    странице (у продолжения бывает другое число колонок из-за объединённых ячеек шапки)."""
    out: dict[str, list[list[str]]] = {}
    prev, prev_cols, prev_page = None, 0, -1
    for page, rows in tables:
        if not rows:
            continue
        kind = classify(rows)
        cols = max(len(r) for r in rows)
        adjacent = page in (prev_page, prev_page + 1)
        first_on_page = page != prev_page
        if kind is None and prev is not None and adjacent and (cols == prev_cols or (prev == "volume" and first_on_page)):
            out[prev].extend(rows)
            if sources is not None:
                sources[prev]["pages"] = sorted(set(sources[prev]["pages"] + [page + 1]))
            prev_page, prev_cols = page, cols
            continue
        prev, prev_cols, prev_page = (kind, cols, page) if kind and kind != "other" else (None, 0, page)
        if prev:
            # Повторная шапка: например, темы второго семестра той же дисциплины.
            out.setdefault(prev, []).extend(rows)
            if sources is not None:
                source = sources.setdefault(prev, {"method": "table", "pages": []})
                source["pages"] = sorted(set(source["pages"] + [page + 1]))
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" .;")


def _hours(s: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", s or "")
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return int(v) if v.is_integer() else v


def _numbered(rows: list[list[str]]) -> list[list[str]]:
    """Строки с номером в первой колонке (строки шапки и «Итого» отбрасываются)."""
    return [r for r in rows if r and re.fullmatch(r"\d{1,2}\.?", r[0].strip()) and any(c.strip() for c in r[1:])]


VOLUME_ROWS = [
    ("total", r"общая\s+трудо[её]мкость"),
    ("aud", r"аудиторн|контактн"),
    ("lec", r"^лекци"),
    ("lab", r"лабораторн"),
    ("pr", r"практическ|семинар"),
    ("sr", r"самостоятельн"),
    ("control", r"трудо[её]мкость\s+промежуточн|контроль"),
]


def volume(rows: list[list[str]]) -> dict:
    """Объём дисциплины: часы по видам работы (колонка «Всего»), семестры, вид промежуточной аттестации."""
    hours, sems, control = {}, [], []
    for r in rows[:3]:
        for c in r:
            for n in re.findall(r"семестр№?(\d{1,2})", re.sub(r"\s+", "", c.lower())):
                sems.append(int(n))
    for r in rows:
        label = r[0].lower().replace("ё", "е") if r else ""
        if re.search(r"вид\s+промежуточн", label):
            control = [c for c in r[1:] if c.strip()]
            continue
        for key, pattern in VOLUME_ROWS:
            if key not in hours and re.search(pattern, label):
                v = next((_hours(c) for c in r[1:] if _hours(c) is not None), None)
                if v is not None:
                    hours[key] = v
                break
    # колонка «Всего» перечисляет все формы через запятую; колонки семестров её повторяют
    forms = []
    for c in (control[0].split(",") if control else []):
        c = _clean(c).capitalize()
        if c and c not in forms:
            forms.append(c)
    return {"hours": hours, "semesters": sorted(set(sems)), "control": forms}


def titled(rows: list[list[str]], hours: bool = False) -> list[dict]:
    """Таблица «№ | Наименование | (Краткое содержание или часы)» → [{title, summary} | {title, hours}]."""
    records: list[list[str]] = []
    for row in rows:
        if not row:
            continue
        if re.fullmatch(r"\d{1,2}\.?", row[0].strip()) and len(row) > 1 and row[1].strip():
            if not re.search(r"[A-Za-zА-ЯЁа-яё]", row[1]):
                continue  # строка с номерами колонок
            records.append([str(c or "").strip() for c in row])
        elif records and not row[0].strip() and any(str(c or "").strip() for c in row[1:]):
            # Пустой номер — продолжение предыдущей строки, а не новая тема.
            while len(records[-1]) < len(row):
                records[-1].append("")
            for i, cell in enumerate(row[1:], 1):
                if cell:
                    records[-1][i] = (records[-1][i] + " " + cell.strip()).strip()
    out = []
    for row in records:
        title = _clean(row[1])
        rest = [c for c in row[2:] if c.strip()]
        if hours:
            out.append({"title": title, "hours": _hours(rest[-1]) if rest else None})
        else:
            out.append({"title": title, "summary": _clean(rest[0]) if rest else ""})
    return out


def competencies(rows: list[list[str]]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        m = COMP_CODE.match(_clean(r[0]) if r else "")
        if not m:
            continue
        code = re.sub(r"-\s+", "-", m.group(1))
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "description": _clean(m.group(2))})
    return out


def results(rows: list[list[str]]) -> list[str]:
    """Результаты обучения: «Знать …», «Уметь …», «Владеть …» из последней колонки."""
    out = []
    for r in rows[1:]:
        text = _clean(r[-1]) if r else ""
        for part in re.split(r"(?=\b(?:Знать|Уметь|Владеть)\b)", text):
            part = _clean(part)
            if re.match(r"(Знать|Уметь|Владеть)\b", part) and part not in out:
                out.append(part)
    return learning_results(out)


def practice_period(rows: list[list[str]]) -> dict:
    """«Форма обучения | Период проведения (курс/семестр) | Объём (ЗЕТ) | Продолжительность (недель)»."""
    for r in rows[1:]:
        cells = [_clean(c) for c in r]
        if len(cells) < 4 or not any(re.search(r"\d", c) for c in cells[1:]):
            continue
        sems = [int(n) for n in re.findall(r"(\d{1,2})\s*(?:семестр|сем\.?)", cells[1].lower())]
        if not sems:
            nums = re.findall(r"\d{1,2}", cells[1])
            sems = [int(nums[-1])] if nums else []
        control = [c.strip().capitalize() for c in cells[4].split(",") if c.strip()] if len(cells) > 4 else []
        return {"semesters": sems, "ze": _hours(cells[2]), "weeks": _hours(cells[3]), "control": control}
    return {}


def vkr_topics(rows: list[list[str]]) -> list[str]:
    """Темы ВКР. Абзац текста в рамке тоже опознаётся как таблица тем — у него большинство строк начинается со
    строчной буквы (продолжение предложения), и тогда тем нет."""
    out = []
    for r in rows:
        cells = [_clean(c) for c in r if c.strip()]
        text = next((c for c in cells if not re.fullmatch(r"\d{1,3}\.?", c)), "")
        if text and not classify([r]) and 10 < len(text) < 300:
            out.append(text)
    if not out or sum(1 for t in out if t[:1].islower()) * 2 > len(out):
        return []
    return [t for t in out if not t[:1].islower()][:30]

