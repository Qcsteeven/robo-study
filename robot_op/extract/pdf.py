"""Чтение PDF без офисных приложений: текстовый слой (pdfplumber) и растр страниц (pypdfium2)."""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


class NotPdf(Exception):
    pass


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def check_pdf(data: bytes) -> None:
    """Скачанные документы проверяются по формату и не исполняются (ТЗ, раздел 7)."""
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise NotPdf("Файл не является PDF-документом")


def page_count(data: bytes) -> int:
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def page_texts(data: bytes, max_pages: int | None = None) -> list[str]:
    texts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages):
            if max_pages is not None and i >= max_pages:
                break
            texts.append(page.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
    return texts


def page_words(data: bytes, index: int, scale: float) -> list[Word]:
    """Слова текстового слоя страницы в координатах растра (scale = dpi / 72)."""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[index]
        words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
    return [Word(w["text"], w["x0"] * scale, w["top"] * scale, w["x1"] * scale, w["bottom"] * scale) for w in words]


def render_page(data: bytes, index: int, dpi: int = 300) -> np.ndarray:
    """Страница в оттенках серого (uint8). Для сканов растр берётся из встроенного изображения."""
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[index]
        bitmap = page.render(scale=dpi / 72, grayscale=True)
        img = bitmap.to_pil().convert("L")
    finally:
        pdf.close()
    return np.asarray(img)


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr)
