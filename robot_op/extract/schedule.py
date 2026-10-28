"""Календарный учебный график: сетка видов деятельности по неделям для каждого курса (ТЗ, шаг 7, рисунок 5)."""
from __future__ import annotations

import re
from collections import Counter

from . import pdf as pdfmod
from .grid import Grid, assign_words, binarize, detect_grid
from .ocr import OcrConfig, ocr_cells, ocr_single
from .util import norm, to_int

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["сентябрь", "октябрь", "ноябрь", "декабрь", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август"]
# допустимые обозначения КУГ; «*» — праздничный день, пусто — теоретическое обучение
LEGEND = {
    "Т": "теоретическое обучение",
    "Э": "экзаменационная сессия",
    "У": "учебная практика",
    "П": "производственная практика",
    "Н": "научно-исследовательская работа",
    "Г": "подготовка к сдаче и сдача государственного экзамена",
    "Д": "выполнение и защита ВКР",
    "К": "каникулы",
}
ALLOWED = set(LEGEND) | {"*", ""}
YEAR_RE = re.compile(r"(20\d{2})\s*[/\-–]\s*(20\d{2})")


class NoSchedule(Exception):
    pass


def _month(text: str) -> str | None:
    t = norm(text)
    for m in MONTHS:
        if t.startswith(m[:4]):
            return m.capitalize()
    return None


def _label_col(grid: Grid) -> int | None:
    votes = Counter()
    for r in grid.rows:
        for c in r.cells:
            if norm(c.text) in DAYS:
                votes[c.col0] += 1
    return votes.most_common(1)[0][0] if votes else None


def ocr_grid(arr, grid: Grid, cfg: OcrConfig) -> None:
    dark = binarize(arr)
    inset = grid.line_px + 1
    left = [c for r in grid.rows for c in r.cells if c.col0 <= 4]
    first = [c for c in grid.rows[0].cells if c.col0 > 4]
    cells = left + first
    for c, t in zip(cells, ocr_cells(arr, [c.box for c in cells], cfg, "rus", inset, dark)):
        c.text = t
    label = _label_col(grid)
    if label is None:
        return
    weeks_row, course_rows = _structure(grid, label)
    if weeks_row is not None:
        wc = [c for c in grid.rows[weeks_row].cells if c.col0 > label]
        for n, (c, t) in enumerate(zip(wc, ocr_cells(arr, [c.box for c in wc], cfg, "digits", inset, dark)), 1):
            c.text = t
            # номер недели известен заранее — сомнительные ячейки перечитываются по одной, в т. ч. с увеличением
            for scale in (1.0, 2.0, 3.0):
                if to_int(c.text) == n:
                    break
                c.text = ocr_single(arr, c.box, cfg, "digits", inset, dark, scale) or c.text
    body = [c for ri in course_rows for c in grid.rows[ri].cells if c.col0 > label]
    for c, t in zip(body, ocr_cells(arr, [c.box for c in body], cfg, "kug", inset, dark)):
        c.text = t


def _structure(grid: Grid, label: int) -> tuple[int | None, list[int]]:
    """Строка номеров недель и строки курсов (после шапки с датами, где есть «вс»)."""
    labels = [norm(r.text_at(label)) for r in grid.rows]
    last_sunday = max((i for i, t in enumerate(labels) if t == "вс"), default=-1)
    weeks_row = last_sunday + 1 if 0 <= last_sunday + 1 < len(grid.rows) and labels[last_sunday + 1] not in DAYS else None
    start = (weeks_row + 1) if weeks_row is not None else last_sunday + 1
    course_rows = [i for i in range(start, len(grid.rows)) if labels[i] in DAYS]
    return weeks_row, course_rows


def parse_grid(grid: Grid) -> dict:
    label = _label_col(grid)
    if label is None:
        raise NoSchedule("Не найдена колонка дней недели")
    weeks_row, course_rows = _structure(grid, label)
    week_cols = [i for i in range(label + 1, len(grid.columns))]
    messages = []
    # номера недель
    if weeks_row is not None:
        nums = [to_int(grid.rows[weeks_row].text_at(c)) for c in week_cols]
        bad = [i + 1 for i, n in enumerate(nums) if n != i + 1]
        if bad:
            messages.append(f"Номера недель распознаны не полностью: позиции {', '.join(map(str, bad[:10]))}")
    # месяцы
    months: list[list] = []
    for c in grid.rows[0].cells:
        if c.col1 <= label:
            continue
        name = _month(c.text)
        span = c.col1 - max(c.col0, label + 1) + 1
        if name and span > 0:
            months.append([name, span])
    # курсы: блоки подряд идущих строк пн…сб; номер курса и группа — в объединённых ячейках слева
    courses = [_course(grid, block, label, week_cols, messages) for block in _course_blocks(grid, course_rows)]
    if not courses:
        raise NoSchedule("Не найдены строки курсов")
    if len(week_cols) not in (52, 53):
        messages.append(f"Число недель учебного года {len(week_cols)} (ожидается 52)")
    return {"months": months, "weeks": len(week_cols), "courses": courses, "messages": messages}


def _course_blocks(grid: Grid, course_rows: list[int]) -> list[list[int]]:
    """Строки одного курса — те, что лежат по высоте внутри объединённой ячейки «Курс»."""
    first_col = [c for r in grid.rows for c in r.cells if c.col0 == 0]
    blocks: list[list[int]] = []
    keys: list[int] = []
    for ri in course_rows:
        row = grid.rows[ri]
        mid = (row.y0 + row.y1) / 2
        owner = next((id(c) for c in first_col if c.y0 - 2 <= mid <= c.y1 + 2), ri)
        if keys and keys[-1] == owner:
            blocks[-1].append(ri)
        else:
            keys.append(owner)
            blocks.append([ri])
    return blocks


def _course(grid: Grid, rows: list[int], label: int, week_cols: list[int], messages: list[str]) -> dict:
    course, group = "", ""
    for ri in rows:
        for c in grid.rows[ri].cells:
            if c.col1 < label and c.text.strip():
                if c.col0 == 0 and not course:
                    course = re.sub(r"\D", "", c.text.translate(str.maketrans("ЗзОоIl|", "3300111"))) or c.text.strip()
                elif not group:
                    group = re.sub(r"(?<=[А-ЯЁа-яё])6(?=-?\d)", "б", c.text.strip())
    weeks = []
    unknown = set()
    for col in week_cols:
        codes = []
        for ri in rows:
            t = grid.rows[ri].text_at(col).strip().upper()[:1]
            if t not in ALLOWED:
                unknown.add(t)
            codes.append(t)
        real = [c for c in codes if c and c != "*" and c in LEGEND]
        code = Counter(real).most_common(1)[0][0] if real and len(real) * 2 >= len([c for c in codes if c != "*"]) else "Т"
        weeks.append(code)
    if unknown:
        messages.append(f"Курс {course}: недопустимые обозначения {', '.join(sorted(unknown))}")
    return {"course": course, "group": group, "weeks": "".join(weeks)}


def extract(data: bytes, scanned: bool, cfg: OcrConfig, dpi: int = 300) -> dict:
    title_text = ""
    result = None
    for i in range(pdfmod.page_count(data)):
        arr = pdfmod.render_page(data, i, dpi)
        grid = detect_grid(arr)
        if grid is None:
            continue
        if scanned:
            ocr_grid(arr, grid, cfg)
            from .ocr import ocr_page_text
            top = arr[: max(1, grid.rows[0].y0 - 4)]
            title_text = ocr_page_text(top, cfg) if top.shape[0] > 20 else ""
        else:
            assign_words(grid, pdfmod.page_words(data, i, dpi / 72))
            title_text = (pdfmod.page_texts(data)[i] or "")[:500]
        try:
            result = parse_grid(grid)
            break
        except NoSchedule:
            continue
    if result is None:
        raise NoSchedule("Сетка календарного учебного графика не найдена")
    m = YEAR_RE.search(title_text or "")
    result["academic_year"] = f"{m.group(1)}/{m.group(2)}" if m else ""
    result["title"] = " ".join((title_text or "").split())[:300]
    result["legend"] = [{"code": k, "label": v} for k, v in LEGEND.items()]
    if not result["academic_year"]:
        result["messages"].append("Не распознан учебный год в заголовке")
    return result
