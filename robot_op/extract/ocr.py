"""Модуль OCR: распознавание ячеек таблицы средствами Tesseract 5.

Каждая ячейка вырезается без линий сетки и очищается от шума. Однострочные ячейки с числами и
обозначениями КУГ распознаются пакетно: они складываются столбиком на одном листе с промежутками,
лист распознаётся одним вызовом Tesseract со словарём допустимых символов, слова возвращаются
ячейкам по координате y — в десятки раз быстрее вызова на каждую ячейку. Текстовые ячейки (шапка,
наименования) бывают многострочными и разной ширины, в пакете Tesseract теряет в них строки,
поэтому они распознаются по одной, параллельно в несколько потоков.
"""
from __future__ import annotations

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .grid import binarize

DIGITS = "0123456789"
KUG_CHARS = "ТЭУПДКГНтэупдкгн*"
INDEX_CHARS = "БВОДУПНФТКМдпнху0123456789.()"

_GAP = 64
_TOP = 96
_MAX_CANVAS_H = 20000
_BATCH_MODES = {"digits", "kug"}
# Tesseract не должен сам распараллеливаться: потоки запускаются здесь
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


class OcrUnavailable(Exception):
    pass


@dataclass
class OcrConfig:
    tesseract_cmd: str = "tesseract"
    lang: str = "rus+eng"


def _tess(cfg: OcrConfig):
    try:
        import pytesseract
    except ImportError as e:   # pragma: no cover
        raise OcrUnavailable("Не установлен pytesseract") from e
    cmd = shutil.which(cfg.tesseract_cmd) or cfg.tesseract_cmd
    if not shutil.which(cmd):
        raise OcrUnavailable(f"Не найден Tesseract ({cfg.tesseract_cmd})")
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def crop_ink(arr: np.ndarray, box: tuple[int, int, int, int], inset: int, dark: np.ndarray | None = None,
             min_ink: float = 0.003) -> np.ndarray | None:
    """Ячейка без линий сетки, обрезанная по содержимому; None — пустая ячейка."""
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    d = (dark if dark is not None else binarize(arr))[y0:y1, x0:x1]
    if d.mean() < min_ink:
        return None
    ys, xs = np.nonzero(d)
    if ys.size < 6:
        return None
    pad = 6
    cy0, cy1 = max(0, ys.min() - pad), min(d.shape[0], ys.max() + pad + 1)
    cx0, cx1 = max(0, xs.min() - pad), min(d.shape[1], xs.max() + pad + 1)
    crop = np.where(d[cy0:cy1, cx0:cx1], 0, 255).astype(np.uint8)
    return crop


def ocr_cells(arr: np.ndarray, boxes: list[tuple[int, int, int, int]], cfg: OcrConfig, mode: str = "text",
              inset: int = 3, dark: np.ndarray | None = None) -> list[str]:
    """Распознаёт ячейки; mode: text — слова, digits — числа, kug — обозначения КУГ."""
    tess = _tess(cfg)
    dark = binarize(arr) if dark is None else dark
    crops = [crop_ink(arr, b, inset, dark) for b in boxes]
    result = [""] * len(boxes)
    todo = [(i, c) for i, c in enumerate(crops) if c is not None]
    # мелкие буквы увеличиваются; цифры — нет: при увеличении LSTM путает одиночные «7» и «4»
    if mode != "digits":
        todo = [(i, _scale(c, 2.0) if c.shape[0] < 40 else c) for i, c in todo]
    lang, config = _mode_config(mode, cfg)
    if mode not in _BATCH_MODES:
        def one(item):
            i, crop = item
            text = tess.image_to_string(Image.fromarray(_pad(crop, 20)), lang=lang, config=config)
            return i, " ".join(w for w in text.split() if not _NOISE.match(w))
        with ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2))) as pool:
            for i, text in pool.map(one, todo):
                result[i] = text
        return result
    batch: list[tuple[int, np.ndarray]] = []
    height = 0
    for item in todo:
        h = item[1].shape[0] + _GAP
        if batch and height + h > _MAX_CANVAS_H:
            _run_batch(tess, batch, lang, config, result)
            batch, height = [], 0
        batch.append(item)
        height += h
    if batch:
        _run_batch(tess, batch, lang, config, result)
    # ячейки с содержимым, которые в пакете не распознались, — повторно по одной (одиночная строка/знак)
    for i, crop in todo:
        if not result[i].strip():
            for psm in (7, 10):
                text = tess.image_to_string(Image.fromarray(_pad(crop, 20)), lang=lang,
                                            config=config.replace("--psm 6", f"--psm {psm}")).strip()
                if text:
                    result[i] = " ".join(text.split())
                    break
    if mode == "kug":
        result = [_kug_code(t) for t in result]
    return result


def ocr_single(arr: np.ndarray, box: tuple[int, int, int, int], cfg: OcrConfig, mode: str = "digits",
               inset: int = 3, dark: np.ndarray | None = None, scale: float = 1.0) -> str:
    """Повторное распознавание одной ячейки однострочным режимом — для значений, не прошедших проверку."""
    tess = _tess(cfg)
    crop = crop_ink(arr, box, inset, binarize(arr) if dark is None else dark)
    if crop is None:
        return ""
    if scale != 1.0:
        crop = _scale(crop, scale)
    lang, config = _mode_config(mode, cfg)
    text = tess.image_to_string(Image.fromarray(_pad(crop, 20)), lang=lang, config=config.replace("--psm 6", "--psm 7"))
    return " ".join(text.split())


def _mode_config(mode: str, cfg: OcrConfig) -> tuple[str, str]:
    config = "--psm 6 -c preserve_interword_spaces=1"
    if mode == "digits":
        return "eng", config + f" -c tessedit_char_whitelist={DIGITS}"
    if mode == "kug":
        return "rus", config + f" -c tessedit_char_whitelist={KUG_CHARS}"
    if mode == "rus":
        return "rus", config
    if mode == "index":
        return "rus", config + f" -c tessedit_char_whitelist={INDEX_CHARS}"
    return cfg.lang, config


def _pad(crop: np.ndarray, k: int) -> np.ndarray:
    return np.pad(crop, k, constant_values=255)


def _scale(crop: np.ndarray, k: float) -> np.ndarray:
    img = Image.fromarray(crop)
    return np.asarray(img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS))


def _run_batch(tess, batch, lang, config, result) -> None:
    width = max(c.shape[1] for _, c in batch) + 2 * _GAP
    # большой верхний отступ: Tesseract в режиме блока текста склонен терять строку у самого края листа
    total = sum(c.shape[0] + _GAP for _, c in batch) + _TOP + _GAP
    canvas = np.full((total, width), 255, dtype=np.uint8)
    spans = []
    y = _TOP
    for i, c in batch:
        canvas[y:y + c.shape[0], _GAP:_GAP + c.shape[1]] = c
        spans.append((i, y, y + c.shape[0]))
        y += c.shape[0] + _GAP
    data = tess.image_to_data(Image.fromarray(canvas), lang=lang, config=config, output_type=tess.Output.DICT)
    # слова группируются по строкам самого Tesseract (блок, абзац, строка) и возвращаются ячейке по y
    lines: dict[tuple, list] = {}
    for n, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text or _NOISE.match(text):
            continue
        key = (data["block_num"][n], data["par_num"][n], data["line_num"][n])
        lines.setdefault(key, []).append((data["word_num"][n], data["left"][n], data["top"][n], data["height"][n], text))
    words: dict[int, list[tuple[float, str]]] = {}
    for ws in lines.values():
        ws.sort()
        cy = sorted(top + h / 2 for _, _, top, h, _ in ws)[len(ws) // 2]
        for i, y0, y1 in spans:
            if y0 - _GAP / 2 <= cy <= y1 + _GAP / 2:
                words.setdefault(i, []).append((cy, " ".join(w[-1] for w in ws)))
                break
    for i, ls in words.items():
        result[i] = " ".join(t for _, t in sorted(ls))


_NOISE = re.compile(r"^[|\\/\-—–_`'\".,:;°~^•]+$|^[a-zA-Z]$")


def _kug_code(text: str) -> str:
    t = text.replace(" ", "").upper()
    for ch in t:
        if ch in "ТЭУПДКГН*":
            return ch
    return t[:1]


def ocr_page_text(arr: np.ndarray, cfg: OcrConfig, psm: int = 6) -> str:
    tess = _tess(cfg)
    return tess.image_to_string(Image.fromarray(arr), lang=cfg.lang, config=f"--psm {psm}")
