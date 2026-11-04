"""Календарный учебный график: виды деятельности по дням и по неделям для каждого курса (ТЗ, шаг 7, рисунок 5)."""
from __future__ import annotations

import re
from collections import Counter

from . import pdf as pdfmod
from .grid import Cell, Row, FineRaster, Grid, assign_words, binarize, detect_grid, rotate, skew_angle
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
    "Пд": "производственная (преддипломная) практика",
    "Н": "научно-исследовательская работа",
    "Г": "подготовка к сдаче и сдача государственного экзамена",
    "Д": "выполнение и защита ВКР",
    "К": "каникулы",
    "НИ": "научные исследования", "ПА": "промежуточная аттестация аспиранта",
    "ИА": "итоговая аттестация аспиранта", "Пр": "практика аспирантов",
    "ГИА": "государственная итоговая аттестация", "ПДП": "преддипломная практика",
    "unknown": "ячейка не распознана — сверьте с исходным документом",

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


def ocr_grid(arr, grid: Grid, cfg: OcrConfig, fine: FineRaster | None = None) -> None:
    dark = binarize(arr)
    inset = grid.line_px + 1

    def read(cells, mode):
        if fine is not None:
            return ocr_cells(fine.arr, [fine.box(c.box) for c in cells], cfg, mode, inset * fine.k, fine.dark)
        return ocr_cells(arr, [c.box for c in cells], cfg, mode, inset, dark)

    def read_one(c, mode, scale):
        if fine is not None:
            return ocr_single(fine.arr, fine.box(c.box), cfg, mode, inset * fine.k, fine.dark, scale)
        return ocr_single(arr, c.box, cfg, mode, inset, dark, scale)

    # колонка дней недели — среди первых колонок; месяцы — в первой строке правее неё
    left = [c for r in grid.rows for c in r.cells if c.col0 <= 4]
    for c, t in zip(left, read(left, "rus")):
        c.text = t
    label = _label_col(grid)
    if label is None:
        # Сводный график СПО: читаем подписи отдельно от обозначений недель.
        heads = [c for r in grid.rows[:4] for c in r.cells]
        for c, t in zip(heads, read(heads, "rus")):
            c.text = t
        group_col = next((c.col0 for r in grid.rows[:4] for c in r.cells if "групп" in norm(c.text)), 2)
        labels = [c for r in grid.rows[1:] for c in r.cells if c.col0 <= group_col]
        for c, t in zip(labels, read(labels, "rus")):
            c.text = t
        body = [c for r in grid.rows[4:] for c in r.cells if c.col0 > group_col]
        for c, t in zip(body, read(body, "kug_full")):
            c.text = t
        return
    first = [c for c in grid.rows[0].cells if c.col0 > label]
    for c, t in zip(first, read(first, "rus")):
        c.text = t
    weeks_row, course_rows = _structure(grid, label)
    if weeks_row is not None:
        wc = [c for c in grid.rows[weeks_row].cells if c.col0 > label]
        for n, (c, t) in enumerate(zip(wc, read(wc, "digits")), 1):
            c.text = t
            # номер недели известен заранее — сомнительные ячейки перечитываются по одной, в т. ч. с увеличением
            for scale in (1.0, 2.0, 3.0):
                if to_int(c.text) == n:
                    break
                c.text = read_one(c, "digits", scale) or c.text
    body = [c for ri in course_rows for c in grid.rows[ri].cells if c.col0 > label]
    for c, t in zip(body, read(body, "kug")):
        c.text = t
    if sum(c.text == "НИ" for c in body) > 30:
        extended = {"НИ", "ПА", "ИА", "Пр", "К", "Э", "Т", "*", ""}
        for c in body:
            if c.text not in extended:
                for mode in ("rus", "kug"):
                    value = _code(read_one(c, mode, 2.0))
                    if value in extended and value:
                        c.text = value
                        break


def _structure(grid: Grid, label: int) -> tuple[int | None, list[int]]:
    """Строка номеров недель и строки курсов.

    Шапку (месяцы, даты по дням недели, номера недель) накрывает объединённая ячейка «Курс» в первой
    колонке; строки ниже неё — курсы. Если такой ячейки нет, шапка заканчивается строкой «вс»
    (в реальных графиках она напечатана бледно и может не распознаться)."""
    labels = [norm(r.text_at(label)) for r in grid.rows]
    head = next((c for c in grid.rows[0].cells if c.col0 == 0), None) if grid.rows else None
    if head is not None and head.y1 > grid.rows[0].y1 + 5:
        last = max(i for i, r in enumerate(grid.rows) if (r.y0 + r.y1) / 2 <= head.y1 + 2)
        weeks_row = last if last > 0 and labels[last] not in DAYS else None
        return weeks_row, _day_rows(labels, last + 1)
    last_sunday = max((i for i, t in enumerate(labels) if t == "вс"), default=-1)
    weeks_row = last_sunday + 1 if 0 <= last_sunday + 1 < len(grid.rows) and labels[last_sunday + 1] not in DAYS else None
    start = (weeks_row + 1) if weeks_row is not None else last_sunday + 1
    return weeks_row, _day_rows(labels, start)


def _day_rows(labels: list[str], start: int) -> list[int]:
    """Строки курсов — от первой до последней строки с подписью дня; строки между ними, чью подпись OCR не
    распознал, тоже курсовые. Если таблица начинается не с «пн» или кончается не на «сб», недостающие
    соседние строки добавляются."""
    found = [i for i in range(start, len(labels)) if labels[i] in DAYS]
    if not found:
        return []
    first, last = found[0], found[-1]
    if labels[first] != "пн":
        first = max(start, first - DAYS.index(labels[first]))
    if labels[last] != "сб" and labels[last] in DAYS[:6]:
        last = min(len(labels) - 1, last + 5 - DAYS.index(labels[last]))
    return list(range(first, last + 1))


def parse_grid(grid: Grid) -> dict:
    label = _label_col(grid)
    if label is None:
        return _weekly_grid(grid)
    weeks_row, course_rows = _structure(grid, label)
    week_cols = [i for i in range(label + 1, len(grid.columns))]
    day_cols = list(week_cols)   # по дням — все колонки, включая хвост августа без номера недели
    messages = []
    # номера недель
    if weeks_row is not None:
        nums = [to_int(grid.rows[weeks_row].text_at(c)) for c in week_cols]
        # за 52-й неделей бывает заштрихованная колонка без номера — остаток августа, «обучение не ведётся»
        while len(week_cols) > 52 and nums and nums[-1] is None:
            week_cols.pop()
            nums.pop()
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
    courses = [_course(grid, block, label, week_cols, day_cols, messages) for block in _course_blocks(grid, course_rows)]
    if not courses:
        raise NoSchedule("Не найдены строки курсов")
    # номер курса — одна цифра в высокой узкой ячейке, OCR её иногда не видит; курсы идут по порядку
    known = [(i, int(c["course"])) for i, c in enumerate(courses) if c["course"].isdigit()]
    if known and all(n - i == known[0][1] - known[0][0] for i, n in known):
        for i, c in enumerate(courses):
            c["course"] = c["course"] or str(known[0][1] - known[0][0] + i)
    _fix_days(courses)
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


def _course(grid: Grid, rows: list[int], label: int, week_cols: list[int], day_cols: list[int],
            messages: list[str]) -> dict:
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
            t = _code(grid.rows[ri].text_at(col))
            if t not in ALLOWED:
                unknown.add(t)
            codes.append(t)
        real = [c for c in codes if c and c != "*" and c in LEGEND]
        code = Counter(real).most_common(1)[0][0] if real and len(real) * 2 >= len([c for c in codes if c != "*"]) else "unknown" if sum(c not in ALLOWED for c in codes) * 2 >= len(codes) else "Т"
        weeks.append(code)
    if unknown:
        messages.append(f"Курс {course}: недопустимые обозначения {', '.join(sorted(unknown))}")
    # как в документе: обозначение каждого дня; «*» — нерабочий праздничный день, пусто — теоретическое обучение.
    # Нераспознанное (штриховка «обучение не ведётся» за пределами учебного года) — пусто
    days = {}
    for k, ri in enumerate(rows):
        # строки курса идут по порядку пн…сб; подпись дня OCR иногда не видит
        day = DAYS[k] if len(rows) == 6 else norm(grid.rows[ri].text_at(label))
        if day in DAYS and day not in days:
            codes = [_code(grid.rows[ri].text_at(col)) for col in day_cols]
            days[day] = [c if c in ALLOWED else "unknown" for c in codes]
    return {"course": course, "group": group, "weeks": weeks, "days": days}


def _fix_days(courses: list[dict]) -> None:
    """Сверка обозначений по дням со структурой графика — исправляет одиночные ошибки OCR.

    Нерабочие праздничные дни общие для всех курсов: «*» ставится там, где его видит большинство курсов, и
    снимается, где его видит меньшинство. Внутри недели у курса один вид деятельности: день, который один
    расходится с остальными (не менее четырёх одинаковых рабочих дней той же недели), получает их обозначение."""
    grids = [c["days"] for c in courses if c.get("days")]
    if not grids:
        return
    for day in DAYS:
        rows = [g[day] for g in grids if day in g]
        if len(rows) < 2:
            continue
        for i in range(max(len(r) for r in rows)):
            stars = sum(1 for r in rows if i < len(r) and r[i] == "*")
            holiday = stars * 2 > len(rows)
            for r in rows:
                if i >= len(r):
                    continue
                if holiday:
                    r[i] = "*"
                elif r[i] == "*" and stars * 2 < len(rows):
                    r[i] = ""
    for g in grids:
        n = max(len(v) for v in g.values())
        for i in range(n):
            week = {d: g[d][i] for d in g if i < len(g[d]) and g[d][i] != "*"}
            for d, code in week.items():
                others = [v for k, v in week.items() if k != d]
                if len(others) >= 4 and len(set(others)) == 1 and code != others[0]:
                    g[d][i] = others[0]


def _code(text: str) -> str:
    t = text.replace(" ", "").upper()
    if t in ("ПР", "Пд".upper()):
        return "Пр" if t == "ПР" else "Пд"
    if t in LEGEND or re.fullmatch(r"(?:УП|ПП)(?:\.\d+)?", t):
        return t
    return t


def _weekly_grid(grid: Grid) -> dict:
    group_votes = Counter(c.col0 for r in grid.rows for c in r.cells if re.search(r"[-–]\d{2}[-–]\d", c.text))
    if group_votes:
        label = group_votes.most_common(1)[0][0]
        first_group = next(i for i,r in enumerate(grid.rows) if re.search(r"[-–]\d{2}[-–]\d", r.text_at(label)))
        header = first_group - 1
    else:
        header = next((i for i, r in enumerate(grid.rows) if any(re.search(r"г[рл]уп", norm(c.text)) for c in r.cells)), None)
        if header is None:
            raise NoSchedule("Не найдены дни недели или шапка групп")
        label = next(c.col0 for c in grid.rows[header].cells if re.search(r"г[рл]уп", norm(c.text)))
    week_cols = list(range(label + 1, len(grid.columns)))
    courses = []
    messages = []
    inherited_code = ""
    for r in grid.rows[header + 1:]:
        group = r.text_at(label).strip()
        if not re.search(r"[-–]\d{2}[-–]\d", group):
            continue
        code = next((c.text.strip() for c in r.cells if c.col1 < label and re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", c.text.strip())), "")
        inherited_code = code or inherited_code
        code = inherited_code
        values = [_code(r.text_at(c)) or "Т" for c in week_cols]
        unknown = [i for i,v in enumerate(values) if v not in ALLOWED and not re.fullmatch(r"(?:УП|ПП)(?:\.\d+)?", v)]
        if unknown:
            messages.append(f"Группа {group}: не распознано ячеек {len(unknown)}")
            for i in unknown:
                values[i] = "unknown"
        courses.append({"course": "", "group": group, "program_code": code, "weeks": values, "days": {}})
    if not courses:
        raise NoSchedule("Не найдены строки учебных групп")
    months = [[_month(c.text), c.col1 - max(c.col0, label + 1) + 1] for r in grid.rows[:header + 1] for c in r.cells if _month(c.text) and c.col1 > label]
    return {"months": months, "weeks": len(week_cols), "courses": courses, "mode": "weekly", "messages": messages}


def _regular_calendar_grid(grid: Grid) -> Grid:
    """В календарной сетке равная ширина недель. Отсекает штрихи букв,
    ошибочно принятые за вертикальные линии в очень низких строках скана."""
    widths = [c.x1-c.x0 for r in grid.rows for c in r.cells if 20 <= c.x1-c.x0 <= 160]
    if not widths:
        return grid
    mode = Counter(round(w / 10) * 10 for w in widths).most_common(1)[0][0]
    candidates = []
    for i, row in enumerate(grid.rows):
        cells = [c for c in row.cells if mode * .8 <= c.x1-c.x0 <= mode * 1.2]
        if len(cells) < 48:
            continue
        gaps = [b.x0-a.x0 for a,b in zip(cells,cells[1:])]
        if not gaps or max(gaps) > mode * 1.7:
            continue
        candidates.append((len(cells)*3 - len(row.cells), -i, cells))
    if not candidates:
        return grid
    _, minus_i, regular = max(candidates, key=lambda x: x[:2])
    reference = -minus_i
    prefix = [c for c in grid.rows[0].cells if c.x1 < regular[0].x0]
    columns = [(c.x0,c.x1) for c in prefix + regular]
    # Сетка уже правильная: сохраняем её объединённые ячейки.
    if len(grid.columns) == len(columns):
        return grid
    rows = []
    left = len(prefix)
    for ri, row in enumerate(grid.rows):
        cells = []
        for c in row.cells:
            if c.x1 < regular[0].x0:
                indices = [j for j,(a,b) in enumerate(columns[:left]) if a <= (c.x0+c.x1)/2 <= b]
                if indices:
                    j=indices[0]; cells.append(Cell(c.x0,c.x1,c.y0,c.y1,j,j,c.text))
        if ri < reference:
            used=set()
            for c in row.cells:
                indices=[j for j,(a,b) in enumerate(columns) if j>=left and c.x0-3 <= (a+b)/2 <= c.x1+3]
                if indices:
                    lo,hi=indices[0],indices[-1]
                    if not any(j in used for j in indices):
                        cells.append(Cell(columns[lo][0],columns[hi][1],c.y0,c.y1,lo,hi,c.text)); used.update(indices)
        else:
            cells.extend(Cell(a,b,row.y0,row.y1,j,j) for j,(a,b) in enumerate(columns) if j>=left)
        rows.append(Row(row.y0,row.y1,cells))
    return Grid(rows,columns,grid.line_px)


def extract(data: bytes, scanned: bool, cfg: OcrConfig, dpi: int = 300) -> dict:
    title_text = ""
    result = None
    for i in range(pdfmod.page_count(data)):
        arr = pdfmod.render_page(data, i, dpi)
        angle = skew_angle(arr) if scanned else 0.0
        arr = rotate(arr, angle)
        grid = detect_grid(arr)
        if grid is None:
            continue
        grid = _regular_calendar_grid(grid)
        if scanned:
            ocr_grid(arr, grid, cfg, FineRaster.for_scan(data, i, dpi, angle))
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
    if not m and scanned:
        # Печать и подписи над таблицей мешают блочному OCR; заголовок читаем
        # также как разреженный текст, не объединяя его со штампом.
        sparse = ocr_page_text(top, cfg, psm=11)
        compact = re.sub(r"(?<=\d)\s+(?=\d)", "", sparse)
        m = YEAR_RE.search(compact)
        if m:
            title_text = sparse
    result["academic_year"] = f"{m.group(1)}/{m.group(2)}" if m else ""
    result["title"] = " ".join((title_text or "").split())[:300]
    result["legend"] = [{"code": k, "label": v} for k, v in LEGEND.items()]
    if not result["academic_year"]:
        result["messages"].append("Не распознан учебный год в заголовке")
    return result
