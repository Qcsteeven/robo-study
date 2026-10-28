"""Восстановление структуры таблицы по растру страницы: линии сетки → строки → ячейки.

Горизонтальные линии ищутся как длинные непрерывные серии тёмных пикселей. Вертикальные линии
определяются отдельно в каждой полосе между горизонтальными линиями: линия должна пересекать полосу
почти целиком, поэтому штрихи букв за линии не принимаются, а объединённые ячейки шапки
(«Семестр 1 [16 нед]» над «Лек | Лаб | Пр») получаются сами собой.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .pdf import Word


@dataclass
class Cell:
    x0: int
    x1: int
    y0: int
    y1: int
    col0: int = 0          # первая и последняя «листовая» колонка, которую накрывает ячейка
    col1: int = 0
    text: str = ""

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x1, self.y1


@dataclass
class Row:
    y0: int
    y1: int
    cells: list[Cell] = field(default_factory=list)

    def texts(self) -> list[str]:
        return [c.text for c in self.cells]

    def cell_at(self, col: int) -> Cell | None:
        for c in self.cells:
            if c.col0 <= col <= c.col1:
                return c
        return None

    def text_at(self, col: int) -> str:
        c = self.cell_at(col)
        return c.text if c else ""


@dataclass
class Grid:
    rows: list[Row]
    columns: list[tuple[int, int]]       # листовые колонки (x0, x1)
    line_px: int = 3

    def all_cells(self):
        for r in self.rows:
            yield from r.cells


def otsu(arr: np.ndarray) -> int:
    hist = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
    total = arr.size
    sum_total = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, threshold = 0.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_total - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, threshold = between, t
    return int(threshold)


def binarize(arr: np.ndarray, despeckle: bool = True) -> np.ndarray:
    t = otsu(arr)
    dark = arr <= min(t, 200)
    if despeckle:
        # одиночные точки скана (пыль, шум) — тёмные пиксели почти без тёмных соседей
        p = np.pad(dark, 1).astype(np.uint8)
        neighbors = sum(p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx]
                        for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        dark &= neighbors >= 2
    return dark


def skew_angle(arr: np.ndarray, max_deg: float = 2.0, step: float = 0.05) -> float:
    """Угол наклона скана (в градусах): при нём горизонтальные линии таблицы дают самый «острый» профиль
    строк. Перебор ведётся по уменьшенному в 4 раза растру."""
    small = np.asarray(Image.fromarray(arr).reduce(4))
    img = Image.fromarray(binarize(small, despeckle=False).astype(np.uint8) * 255)
    best, best_angle = -1.0, 0.0
    for angle in np.arange(-max_deg, max_deg + step / 2, step):
        profile = np.asarray(img.rotate(float(angle), resample=Image.BILINEAR, fillcolor=0), dtype=np.float32).sum(axis=1)
        score = float((profile ** 2).sum())
        if score > best:
            best, best_angle = score, float(angle)
    return best_angle


def rotate(arr: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.03:
        return arr
    return np.asarray(Image.fromarray(arr).rotate(angle, resample=Image.BICUBIC, fillcolor=255))


def deskew(arr: np.ndarray) -> np.ndarray:
    """Выравнивает наклон скана. Даже 0,2° на листе A3 сдвигают линию сетки на 15 px по ширине, и строки
    шапки режутся пополам."""
    return rotate(arr, skew_angle(arr))


class FineRaster:
    """Та же страница, отрисованная в k раз подробнее (сканы ИРНИТУ — 600 dpi) и повёрнутая на тот же угол.
    Мелкие подписи шапки («Семестр 8 [9 нед]») и сомнительные числа читаются по ней; рисуется по запросу."""

    def __init__(self, data: bytes, page: int, dpi: int, angle: float, k: int = 2):
        self._args = (data, page, dpi * k, angle)
        self.k = k
        self._arr = self._dark = None

    @property
    def arr(self) -> np.ndarray:
        if self._arr is None:
            from .pdf import render_page
            data, page, dpi, angle = self._args
            self._arr = rotate(render_page(data, page, dpi), angle)
        return self._arr

    @property
    def dark(self) -> np.ndarray:
        if self._dark is None:
            self._dark = binarize(self.arr)
        return self._dark

    def box(self, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(v * self.k for v in box)

    @classmethod
    def for_scan(cls, data: bytes, page: int, dpi: int, angle: float) -> FineRaster | None:
        """Подробный растр нужен, только если сам скан подробнее отрисовки: скан в 300 dpi при 600 dpi
        лишь размывается, и цифры на нём читаются хуже."""
        from .pdf import scan_dpi
        return cls(data, page, dpi, angle) if scan_dpi(data, page) >= dpi * 1.5 else None


def _runs_mask(dark: np.ndarray, length: int, axis: int) -> np.ndarray:
    """Пиксели, входящие в непрерывную серию тёмных пикселей длиной ≥ length вдоль оси."""
    d = dark if axis == 1 else dark.T
    h, w = d.shape
    if w < length:
        return np.zeros_like(dark)
    c = np.zeros((h, w + 1), dtype=np.int32)
    np.cumsum(d, axis=1, out=c[:, 1:])
    full = (c[:, length:] - c[:, :-length]) == length          # окно [i, i+length) целиком тёмное
    cf = np.zeros((h, full.shape[1] + 1), dtype=np.int32)
    np.cumsum(full, axis=1, out=cf[:, 1:])
    # пиксель x покрыт, если есть полное окно, начинающееся в [x-length+1, x]
    idx = np.arange(w)
    lo = np.clip(idx - length + 1, 0, full.shape[1])
    hi = np.clip(idx + 1, 0, full.shape[1])
    covered = (cf[:, hi] - cf[:, lo]) > 0
    return covered if axis == 1 else covered.T


def _clusters(flags: np.ndarray, min_gap: int = 2) -> list[tuple[int, int]]:
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > min_gap:
            out.append((int(start), int(prev)))
            start = i
        prev = i
    out.append((int(start), int(prev)))
    return out


def detect_grid(arr: np.ndarray, min_row_px: int = 12) -> Grid | None:
    dark = binarize(arr)
    h, w = dark.shape
    run = max(40, w // 60)
    hmask = _runs_mask(dark, run, axis=1)
    counts = hmask.sum(axis=1)
    if counts.max(initial=0) < run * 3:
        return None
    hlines = _clusters(counts >= max(run * 3, int(0.05 * w)))
    if len(hlines) < 2:
        return None
    cols_any = np.flatnonzero(hmask.any(axis=0))
    x_min, x_max = int(cols_any.min()), int(cols_any.max())
    line_px = max(2, int(np.median([b - a + 1 for a, b in hlines])))

    bands: list[tuple[int, int, tuple[int, int], list[tuple[int, int]]]] = []
    for (a0, a1), (b0, b1) in zip(hlines, hlines[1:]):
        y0, y1 = a1 + 1, b0 - 1
        if y1 - y0 < min_row_px:
            continue
        m = max(2, (y1 - y0) // 10)
        band = dark[y0 + m:y1 - m + 1, x_min:x_max + 1]
        ratio = band.mean(axis=0)
        # допуск на небольшой наклон скана: максимум по соседним колонкам
        padded = np.pad(ratio, 2)
        ratio = np.max(np.stack([padded[i:i + ratio.size] for i in range(5)]), axis=0)
        # максимум по 5 колонкам расширяет линию на 2 px с каждой стороны — возвращаем её настоящую ширину,
        # иначе граница ячейки заходит внутрь и у текста, прижатого к рамке, срезается первая буква («О» → «Э»)
        vlines = [(x_min + s + 2, x_min + e - 2) if e - s >= 4 else (x_min + (s + e) // 2,) * 2
                  for s, e in _clusters(ratio >= 0.85)]
        bands.append((y0, y1, (a0, a1), vlines))
    rows: list[Row] = []
    tops: list[tuple[int, int]] = []          # горизонтальная линия над каждой строкой
    all_v: list[tuple[int, int]] = []
    for i, (y0, y1, top, vlines) in enumerate(bands):
        vlines = _continued(vlines, bands, i)
        if len(vlines) < 2:
            continue
        tops.append(top)
        row = Row(y0, y1)
        for (l0, l1), (r0, r1) in zip(vlines, vlines[1:]):
            if r0 - l1 > 4:
                row.cells.append(Cell(l1 + 1, r0 - 1, y0, y1))
        rows.append(row)
        all_v.extend(vlines)
    if not rows:
        return None
    _merge_vertical(rows, tops, dark)
    rows = [r for r in rows if r.cells]
    # листовые колонки: объединение всех вертикальных линий
    flags = np.zeros(w + 1, dtype=bool)
    for s, e in all_v:
        flags[s:e + 1] = True
    vx = _clusters(flags, min_gap=3)
    columns = [(l1 + 1, r0 - 1) for (_, l1), (r0, _) in zip(vx, vx[1:]) if r0 - l1 > 4]
    for row in rows:
        for c in row.cells:
            inside = [i for i, (cx0, cx1) in enumerate(columns) if cx0 >= c.x0 - 3 and cx1 <= c.x1 + 3]
            if inside:
                c.col0, c.col1 = inside[0], inside[-1]
            else:
                mid = (c.x0 + c.x1) / 2
                near = min(range(len(columns)), key=lambda i: abs((columns[i][0] + columns[i][1]) / 2 - mid))
                c.col0 = c.col1 = near
    return Grid(rows, columns, line_px)


def _continued(vlines: list[tuple[int, int]], bands: list, i: int, tol: int = 4) -> list[tuple[int, int]]:
    """Оставляет вертикальные линии, которые продолжаются в соседнюю строку (выше или ниже) через
    горизонтальную линию. Высокий знак внутри низкой ячейки (скобка «[» в «Семестр 8 [9 нед]») похож
    на линию в пределах строки, но в соседнюю строку не переходит. В высокой строке знак текста линией
    не покажется, а её линии могут быть только в ней самой (подписи «Экзамен | Зачёт | КП» между
    объединёнными ячейками), поэтому она не проверяется."""
    if bands[i][1] - bands[i][0] >= 40:
        return vlines
    neighbors = []
    for j in (i - 1, i + 1):
        if 0 <= j < len(bands):
            gap = bands[i][0] - bands[j][1] if j < i else bands[j][0] - bands[i][1]
            if gap < 40 and len(bands[j][3]) >= 2:       # строки разделены одной горизонтальной линией
                neighbors.append(bands[j][3])
    if not neighbors:
        return vlines
    keep = [v for v in vlines
            if any(s - tol <= v[1] and v[0] <= e + tol for other in neighbors for s, e in other)]
    return keep


def _merge_vertical(rows: list[Row], tops: list[tuple[int, int]], dark: np.ndarray) -> None:
    """Объединяет ячейки соседних строк, если между ними нет горизонтальной линии (объединение по вертикали)."""
    carry: list[Cell] = []
    for i, row in enumerate(rows):
        if i == 0:
            carry = list(row.cells)
            continue
        l0, l1 = tops[i]
        adjacent = rows[i - 1].y1 < l0 + 1 and l0 - rows[i - 1].y1 < 6
        keep, next_carry = [], []
        for c in row.cells:
            up = next((u for u in carry if abs(u.x0 - c.x0) <= 6 and abs(u.x1 - c.x1) <= 6), None) if adjacent else None
            # линия над ячейкой есть, если хотя бы одна строка пикселей полосы линии почти целиком тёмная.
            # Лист скана бывает изогнут: у правого края линия лежит на 4–6 px выше или ниже, чем в среднем
            line = dark[max(0, l0 - 6):l1 + 7, c.x0:c.x1 + 1]
            if up is not None and line.mean(axis=1).max(initial=0) < 0.6:
                up.y1 = c.y1
                next_carry.append(up)
            else:
                keep.append(c)
                next_carry.append(c)
        row.cells = keep
        carry = next_carry


def assign_words(grid: Grid, words: list[Word]) -> None:
    """Раскладывает слова текстового слоя по ячейкам (по центру слова)."""
    buckets: dict[int, list[Word]] = {}
    cells = list(grid.all_cells())
    for wd in words:
        for i, c in enumerate(cells):
            if c.x0 - 2 <= wd.cx <= c.x1 + 2 and c.y0 - 2 <= wd.cy <= c.y1 + 2:
                buckets.setdefault(i, []).append(wd)
                break
    for i, ws in buckets.items():
        cells[i].text = join_words(ws)


def join_words(ws: list[Word]) -> str:
    """Слова ячейки по строкам сверху вниз и слева направо."""
    ws = sorted(ws, key=lambda w: (w.cy, w.x0))
    lines: list[list[Word]] = []
    for wd in ws:
        if lines and abs(lines[-1][0].cy - wd.cy) < max(4.0, (wd.y1 - wd.y0) * 0.5):
            lines[-1].append(wd)
        else:
            lines.append([wd])
    return " ".join(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)) for line in lines).strip()
