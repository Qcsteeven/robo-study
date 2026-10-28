"""Учебный план: восстановление таблицы «дисциплины × семестры» (ТЗ, шаг 7, рисунок 4) и проверка (шаг 8)."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import pdf as pdfmod
from .grid import FineRaster, Grid, Row, assign_words, binarize, detect_grid, rotate, skew_angle
from .ocr import OcrConfig, ocr_cells, ocr_single
from .util import clean_text, fix_index, norm, semesters_from_code, to_int

SEM_RE = re.compile(r"семестр\.?\s*(\d{1,2})\s*\.?\s*[\[(]?\s*(\d{1,2})\s*не", re.I)
FIXED = {
    "index": r"^индекс",
    "name": r"^наименован",
    "exam": r"^экза",
    "zach_oc": r"^зач\w*\s*с\s*о|оц\.?$",
    "zach": r"^зач",
    "kp": r"^к[пn]$",
    "kr": r"^к[рp]$",
    "ze": r"^факт|^з\.?\s*е",
    "plan": r"^по\s*план",
    "kont": r"^конт\w*\.?\s*раб",
    "aud": r"^ау[да]",                   # «Ауд.» на скане читается и как «Aya.»
    "control": r"^конт\w*\s*роль|^контроль",
    "sr": r"^ср$|^срс$|^сам",
}
SUB = {"lec": r"^л[еe]к", "lab": r"^л[аa]", "pr": r"^п[рp]|^нр", "eios": r"^эи|^кср|^иф", "sr": r"^с[рp]"}
SUB_ORDER = ["lec", "lab", "pr", "eios", "sr"]
CONTROL_LABELS = {"exam": "Экз", "zach": "Зач", "zach_oc": "ЗаО", "kp": "КП", "kr": "КР"}


@dataclass
class Layout:
    cols: dict[str, int] = field(default_factory=dict)          # FIXED-ключ → листовая колонка
    semesters: dict[int, dict] = field(default_factory=dict)    # номер → {weeks, cols: {lec: col, ...}}
    header_rows: int = 0


class NoTable(Exception):
    pass


def find_layout(grid: Grid) -> Layout:
    lay = Layout()
    sem_spans: list[tuple[int, int, int, int]] = []
    last_header = -1
    for ri, row in enumerate(grid.rows[:8]):
        for c in row.cells:
            t = norm(c.text)
            if not t:
                continue
            sem = _semester(t)
            if sem and c.col1 > c.col0:
                sem_spans.append((*sem, c.col0, c.col1))
                last_header = max(last_header, ri)
                continue
            if c.col0 != c.col1 and not t.startswith(("курс", "форма", "итого")):
                continue
            for key, pattern in FIXED.items():
                if key in lay.cols or not re.search(pattern, t):
                    continue
                if key == "sr" and any(a <= c.col0 <= b for _, _, a, b in sem_spans):
                    break
                lay.cols[key] = c.col0
                last_header = max(last_header, ri)
                break
    if "name" not in lay.cols:
        raise NoTable("Не найдена колонка «Наименование»")
    sem_spans = _renumber(sem_spans)
    for num, weeks, a, b in sem_spans:
        cols: dict[str, int] = {}
        for ri, row in enumerate(grid.rows[:last_header + 2]):
            for c in row.cells:
                if a <= c.col0 <= b and c.col0 == c.col1:
                    t = norm(c.text)
                    for key, pattern in SUB.items():
                        if key not in cols and re.search(pattern, t):
                            cols[key] = c.col0
                            last_header = max(last_header, ri)
                            break
        # нераспознанные подписи подколонок восстанавливаются по стандартному порядку «Лек, Лаб, Пр, (ЭИОС), СР»
        free = [i for i in range(a, b + 1) if i not in cols.values()]
        order = [k for k in SUB_ORDER if k != "eios"] if b - a + 1 == 4 else SUB_ORDER
        missing = [k for k in order if k not in cols]
        if free and len(free) == len(missing):
            cols.update(dict(zip(missing, free)))
        lay.semesters[num] = {"weeks": weeks, "cols": cols}
    lay.header_rows = last_header + 1
    if not lay.semesters:
        raise NoTable("Не найдены колонки семестров")
    return lay


def _semester(t: str) -> tuple[int, int] | None:
    """«Семестр 3 [16 нед]» → (3, 16). Подпись мелкая, OCR путает в ней буквы («Cemectn», «Семесто»)
    и читает скобку как единицу («Семестр 6116 нед»), поэтому слово узнаётся по форме, а числа — по смыслу."""
    m = SEM_RE.search(t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^[сc5][^\s\d]{4,8}\s*(.*)$", t)
    if not m:
        return None
    nums = re.findall(r"\d+", m.group(1))
    if len(nums) >= 2:
        num, weeks = int(nums[0]), int(nums[1][-2:])
    elif nums and len(nums[0]) >= 3:
        num, weeks = int(nums[0][0]), int(nums[0][-2:])
    else:
        return None
    return (num, weeks) if 1 <= num <= 12 and 1 <= weeks <= 30 else None


def _renumber(spans: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Семестры идут слева направо подряд: номер, прочитанный с ошибкой, восстанавливается по порядку."""
    spans = sorted(spans, key=lambda s: s[2])
    if not spans:
        return spans
    start = Counter(num - i for i, (num, *_rest) in enumerate(spans)).most_common(1)[0][0]
    return [(start + i, weeks, a, b) for i, (_, weeks, a, b) in enumerate(spans)]


def ocr_grid(arr, grid: Grid, cfg: OcrConfig, fine: FineRaster | None = None) -> None:
    """OCR в два прохода: шапка и объединённые ячейки — текстом; затем колонки по их смыслу.
    Если есть подробный растр, ячейки читаются по нему, а сетка ищется по обычному."""
    dark = binarize(arr)
    inset = grid.line_px + 1
    head = [c for ri, r in enumerate(grid.rows) for c in r.cells if ri < 6 or c.col1 > c.col0]
    if fine is not None:
        texts = ocr_cells(fine.arr, [fine.box(c.box) for c in head], cfg, "text", inset * fine.k, fine.dark)
    else:
        texts = ocr_cells(arr, [c.box for c in head], cfg, "text", inset, dark)
    for c, t in zip(head, texts):
        c.text = t
    try:
        lay = find_layout(grid)
    except NoTable:
        return
    groups: dict[str, list] = {"index": [], "text": [], "digits": []}
    for r in grid.rows[lay.header_rows:]:
        for c in r.cells:
            if c.col1 > c.col0:
                continue
            if c.col0 == lay.cols.get("index"):
                groups["index"].append(c)
            elif c.col0 == lay.cols["name"]:
                groups["text"].append(c)
            else:
                groups["digits"].append(c)
    for mode, cells in groups.items():
        if fine is not None:
            # при 300 dpi Tesseract заметно чаще теряет цифры, чем по родному разрешению скана
            texts = ocr_cells(fine.arr, [fine.box(c.box) for c in cells], cfg, mode, inset * fine.k, fine.dark)
        else:
            texts = ocr_cells(arr, [c.box for c in cells], cfg, mode, inset, dark)
        for c, t in zip(cells, texts):
            c.text = t


def read_pages(data: bytes, scanned: bool, cfg: OcrConfig, dpi: int = 300) -> list[tuple[Grid, object, FineRaster | None]]:
    pages = []
    for i in range(pdfmod.page_count(data)):
        arr = pdfmod.render_page(data, i, dpi)
        angle = skew_angle(arr) if scanned else 0.0
        arr = rotate(arr, angle)
        grid = detect_grid(arr)
        if grid is None:
            continue
        fine = FineRaster.for_scan(data, i, dpi, angle) if scanned else None
        if scanned:
            ocr_grid(arr, grid, cfg, fine)
        else:
            assign_words(grid, pdfmod.page_words(data, i, dpi / 72))
        pages.append((grid, arr, fine))
    return pages


def recheck_rows(arr, grid: Grid, lay: Layout, items: list[dict], cfg: OcrConfig, fine: FineRaster | None = None) -> int:
    """Строки скана, не прошедшие проверку сумм, перечитываются; новое чтение принимается, только если
    с ним проверка проходит лучше. Возвращает число исправленных строк.

    Tesseract на мелких ячейках иногда теряет цифру («54» → «4»), причём при разных увеличениях — в разных
    ячейках. Поэтому каждая ячейка читается несколько раз, и побеждает самое частое чтение, при равенстве —
    самое длинное (лишнюю цифру OCR добавляет реже, чем теряет)."""
    validate(items)
    fixed = 0
    dark = binarize(arr)
    inset = grid.line_px + 1
    for item in items:
        if not INDEX_RE.match(item["index"]):
            _reread_index(arr, dark, grid, lay, item, cfg, fine)
        if not item["checks"]:
            continue
        row = grid.rows[item["_ri"]]
        before = {id(c): c.text for c in row.cells}
        for c in row.cells:
            if c.col0 == c.col1 and c.col0 not in (lay.cols.get("index"), lay.cols["name"]) and before[id(c)].strip():
                if fine is not None:
                    reads = [before[id(c)]] + [ocr_single(fine.arr, fine.box(c.box), cfg, "digits", inset * fine.k,
                                                          fine.dark, scale=k) for k in (1.0, 1.5, 0.75)]
                else:
                    reads = [before[id(c)]] + [ocr_single(arr, c.box, cfg, "digits", inset, dark, scale=k)
                                               for k in (1.0, 2.0, 3.0)]
                c.text = _vote(reads) or before[id(c)]
        new = _parse_row(row, lay, item["block"], item["part"])
        new["_ri"] = item["_ri"]
        validate([new])
        if len(new["checks"]) < len(item["checks"]):
            item.update(new)
            fixed += 1
        else:
            for c in row.cells:
                c.text = before[id(c)]
    return fixed


INDEX_RE = re.compile(r"^(Б\d(\.([А-Я]{1,2}|\d\d))+(\([А-Яа-я]{1,2}\))?|ФТД\.\d\d)$")


def _reread_index(arr, dark, grid: Grid, lay: Layout, item: dict, cfg: OcrConfig, fine: FineRaster | None) -> None:
    """Индекс, не похожий ни на один допустимый («Б2.В.0П)» вместо «Б2.В.01(П)»), перечитывается по одной
    ячейке; принимается первое чтение допустимого вида."""
    c = grid.rows[item["_ri"]].cell_at(lay.cols["index"]) if "index" in lay.cols else None
    if c is None:
        return
    inset = grid.line_px + 1
    for k in (1.0, 2.0, 3.0):
        if fine is not None:
            text = ocr_single(fine.arr, fine.box(c.box), cfg, "index", inset * fine.k, fine.dark, scale=k)
        else:
            text = ocr_single(arr, c.box, cfg, "index", inset, dark, scale=k)
        index = fix_index(text)
        if INDEX_RE.match(index):
            c.text, item["index"] = text, index
            return


def _vote(reads: list[str]) -> str:
    counts = Counter(r.strip() for r in reads if r and r.strip())
    if not counts:
        return ""
    return max(counts, key=lambda r: (counts[r], len(r)))


# ---------------------------------------------------------------- разбор строк

def _num(row: Row, col: int | None) -> int:
    if col is None:
        return 0
    return to_int(row.text_at(col)) or 0


def parse_grid(grid: Grid, lay: Layout) -> list[dict]:
    rows_out = []
    block, part = "", ""
    idx_col, name_col = lay.cols.get("index"), lay.cols["name"]
    for ri in range(lay.header_rows, len(grid.rows)):
        row = grid.rows[ri]
        name_cell = row.cell_at(name_col)
        if name_cell is None:
            continue
        merged = name_cell.col0 <= (idx_col if idx_col is not None else name_col) and name_cell.col1 >= name_col and name_cell.col1 > name_cell.col0
        text = clean_text(name_cell.text)
        if not text:
            continue
        if norm(text).startswith("итого"):
            break               # под таблицей — итоги и недельная нагрузка, это не дисциплины
        if merged:
            # строка-заголовок: «Блок 1. Дисциплины (модули)», «Обязательная часть», «Часть, формируемая…»
            if norm(text).startswith("блок"):
                block, part = _title_case_block(text), ""
            else:
                part = text
            continue
        item = _parse_row(row, lay, block, part)
        item["_ri"] = ri
        rows_out.append(item)
    return rows_out


def _parse_row(row: Row, lay: Layout, block: str, part: str) -> dict:
    idx_col = lay.cols.get("index")
    item = {
        "index": fix_index(row.text_at(idx_col)) if idx_col is not None else "",
        "name": clean_text(row.text_at(lay.cols["name"])),
        "block": block,
        "part": part,
        "control": {k: semesters_from_code(row.text_at(lay.cols[k])) for k in CONTROL_LABELS if k in lay.cols},
        "ze": _num(row, lay.cols.get("ze")),
        "hours": {k: _num(row, lay.cols.get(k)) for k in ("plan", "kont", "aud", "sr", "control")},
        "semesters": {},
    }
    for num, sem in sorted(lay.semesters.items()):
        vals = {k: _num(row, col) for k, col in sem["cols"].items()}
        if any(vals.values()):
            item["semesters"][str(num)] = vals
    return item


def _title_case_block(text: str) -> str:
    return re.sub(r"(Блок\s*\d)\s*\.?\s*", r"\1. ", text).strip()


def merge_pages(pages: list[tuple[Layout, list[dict]]]) -> tuple[dict, list[dict]]:
    """Страницы УП могут продолжать таблицу вниз (новые строки) или вправо (новые семестры той же строки)."""
    semesters: dict[int, int] = {}
    rows: list[dict] = []
    by_key: dict[str, dict] = {}
    for lay, items in pages:
        for num, sem in lay.semesters.items():
            semesters.setdefault(num, sem["weeks"])
        for it in items:
            key = it["index"] or it["name"]
            if key in by_key:
                old = by_key[key]
                old["semesters"].update(it["semesters"])
                for k, v in it["control"].items():
                    old["control"][k] = sorted(set(old["control"].get(k, [])) | set(v))
                for k, v in it["hours"].items():
                    old["hours"][k] = old["hours"].get(k) or v
                old["ze"] = old["ze"] or it["ze"]
            else:
                by_key[key] = it
                rows.append(it)
    return semesters, rows


# ---------------------------------------------------------------- проверка (ТЗ, шаг 8)

def validate(rows: list[dict]) -> list[str]:
    """Совпадение итоговых часов с суммой часов по семестрам и видам занятий. Документы между собой не сверяются."""
    messages = []
    indexes = [r["index"] for r in rows]
    # строки без з.е.: элективные курсы по физической культуре в з.е. не входят (часы не кратны 36)
    no_ze = {r["index"] for r in rows if r["index"] and not r.get("ze")}
    for r in rows:
        r["is_module"] = bool(r["index"]) and any(i != r["index"] and i.startswith(r["index"] + ".") for i in indexes)
        problems = []
        h = r["hours"]
        sems = r["semesters"].values()
        # «Ауд.» — лекции, лабораторные и практические; работа в ЭИОС входит в «Конт. раб.», но не в «Ауд.»
        aud_sum = sum(s.get("lec", 0) + s.get("lab", 0) + s.get("pr", 0) for s in sems)
        eios_sum = sum(s.get("eios", 0) for s in sems)
        sr_sum = sum(s.get("sr", 0) for s in sems)
        if r["semesters"]:
            if h.get("aud") and h["aud"] not in (aud_sum, aud_sum + eios_sum):
                problems.append(f"«Ауд.» {h['aud']} ≠ сумме по семестрам {aud_sum}")
            if eios_sum and h.get("kont") and h.get("aud") and h["kont"] != h["aud"] + eios_sum:
                problems.append(f"«Конт. раб.» {h['kont']} ≠ «Ауд.» {h['aud']} + ЭИОС {eios_sum}")
            if h.get("sr") and sr_sum and sr_sum != h["sr"]:
                problems.append(f"«СР» {h['sr']} ≠ сумме по семестрам {sr_sum}")
        if h.get("plan"):
            parts = (h.get("kont") or h.get("aud") or 0) + (h.get("sr") or 0) + (h.get("control") or 0)
            if parts and parts != h["plan"]:
                problems.append(f"«По плану» {h['plan']} ≠ конт. раб. + СР + контроль = {parts}")
            has_free_child = r["is_module"] and any(i.startswith(r["index"] + ".") for i in no_ze)
            if not r.get("ze"):
                if h["plan"] % 36 == 0:
                    problems.append("не распознано число з.е.")
            elif r["ze"] * 36 != h["plan"] and not has_free_child:
                problems.append(f"з.е. {r['ze']} × 36 ≠ «По плану» {h['plan']}")
        if not r["name"] or len(r["name"]) < 3:
            problems.append("не распознано наименование")
        r["checks"] = problems
        if problems:
            messages.append(f"{r['index'] or '—'} {r['name']}: " + "; ".join(problems))
    return messages


def extract(data: bytes, scanned: bool, cfg: OcrConfig, dpi: int = 300) -> dict:
    pages = []
    title = ""
    rechecked = 0
    prev: tuple[int, Layout] | None = None
    for grid, arr, fine in read_pages(data, scanned, cfg, dpi):
        try:
            lay = find_layout(grid)
        except NoTable:
            continue
        # шапка на всех страницах плана одна: колонку, подпись которой на этой странице не прочиталась,
        # берём с предыдущей страницы, если сетка та же
        if prev is not None and prev[0] == len(grid.columns):
            for key, col in prev[1].cols.items():
                lay.cols.setdefault(key, col)
        prev = (len(grid.columns), lay)
        items = parse_grid(grid, lay)
        if scanned:
            rechecked += recheck_rows(arr, grid, lay, items, cfg, fine)
        pages.append((lay, items))
    if not pages:
        raise NoTable("Таблица учебного плана не найдена")
    semesters, rows = merge_pages(pages)
    for r in rows:
        r.pop("_ri", None)
    messages = validate(rows)
    if not scanned:
        texts = pdfmod.page_texts(data, max_pages=1)
        title = texts[0].splitlines()[0].strip() if texts and texts[0].strip() else ""
    return {
        "title": title,
        "semesters": [{"number": n, "weeks": w} for n, w in sorted(semesters.items())],
        "rows": rows,
        "checks": {"rows": len(rows), "failed": sum(1 for r in rows if r["checks"]), "messages": messages,
                   "reread": rechecked},
    }


# ---------------------------------------------------------------- для веб-страницы ОП

def pairs_per_week(hours: int, weeks: int) -> float | None:
    """Пары в неделю = часы аудиторных занятий за семестр ÷ 2 ÷ число учебных недель семестра."""
    if not hours or not weeks:
        return None
    return round(hours / 2 / weeks, 1)


def control_label(control: dict, sem: int) -> str:
    return ", ".join(label for key, label in CONTROL_LABELS.items() if sem in control.get(key, []))


def matrix(up: dict) -> dict:
    weeks = {s["number"]: s["weeks"] for s in up.get("semesters", [])}
    groups: list[dict] = []
    for r in up.get("rows", []):
        if r.get("is_module"):
            continue
        m = re.match(r"Блок\s*\d+", r["block"])
        title = f"{m.group(0)}. {r['part']}" if r.get("part") and m else (r.get("part") or r["block"])
        if not groups or groups[-1]["title"] != title:
            groups.append({"title": title, "rows": []})
        cells = {}
        sems = set(int(s) for s in r["semesters"]) | {s for v in r["control"].values() for s in v}
        for s in sorted(sems):
            h = r["semesters"].get(str(s), {})
            cell = {k: pairs_per_week(h.get(k, 0), weeks.get(s, 0)) for k in ("lec", "lab", "pr")}
            cell["control"] = control_label(r["control"], s)
            cell["hours"] = h
            cells[str(s)] = cell
        groups[-1]["rows"].append({"name": r["name"], "index": r["index"], "cells": cells, "checks": r.get("checks", [])})
    return {"semesters": sorted(weeks), "weeks": weeks, "groups": groups}
