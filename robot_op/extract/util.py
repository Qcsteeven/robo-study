"""Нормализация распознанного текста."""
from __future__ import annotations

import re

# латинские буквы, похожие на кириллицу, которые OCR путает в русских подписях
_LAT2CYR = str.maketrans("ABCEHKMOPTXYaceopxyn", "АВСЕНКМОРТХУасеорхуп")


def norm(text: str) -> str:
    """Для сравнения подписей: кириллица, нижний регистр, без лишних пробелов и точек на концах."""
    t = (text or "").translate(_LAT2CYR).lower().replace("ё", "е")
    return re.sub(r"\s+", " ", t).strip(" .:")


def to_int(text: str) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def clean_text(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = t.replace("}", ")").replace("{", "(")
    # OCR иногда отрывает заглавную букву: «И нтеллектуальный» → «Интеллектуальный»
    t = re.sub(r"^([А-ЯЁ]) (?=[а-яё]{3,})", r"\1", t)
    return t


def fix_index(text: str) -> str:
    """Индекс дисциплины УП (Б1.О.01.02, Б2.В.01(Пд)) после OCR."""
    t = (text or "").strip().replace(",", ".").replace(" ", "").strip(".-")
    if not t:
        return ""
    t = t.translate(str.maketrans("BOYHM", "ВОУНП"))
    if re.match(r"^\d\.", t):
        t = "Б" + t            # буква «Б» не распознана
    elif t[0] in "6bБ5":
        t = "Б" + t[1:]
    t = re.sub(r"^Б+", "Б", t)
    # буква части плана после «Бn.»: О (обязательная), В (вариативная), Б (базовая), ДВ, Ф
    t = re.sub(r"^(Б\d)\.0+\.", r"\1.О.", t)
    t = re.sub(r"^(Б\d)\.8\.", r"\1.В.", t)
    return t


def semesters_from_code(text: str) -> list[int]:
    """«58» → [5, 8]; «1234» → [1, 2, 3, 4]; семестры 10–12 в УП обозначаются буквами A, B, C."""
    out = []
    for ch in (text or "").upper():
        if ch.isdigit() and ch != "0":
            out.append(int(ch))
        elif ch in "ABCАВС":
            out.append(10 + "ABC".find(ch) if ch in "ABC" else 10 + "АВС".find(ch))
    return sorted(set(out))
