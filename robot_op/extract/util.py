"""Нормализация распознанного текста."""
from __future__ import annotations

import re

# латинские буквы, похожие на кириллицу, которые OCR путает в русских подписях
_LAT2CYR = str.maketrans("ABCEHKMNOPTXYaceopxyn", "АВСЕНКМПОРТХУасеорхуп")


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
    # перенос в ячейке: «проектно- технологическая» → «проектно-технологическая»
    t = re.sub(r"(?<=[а-яё])- (?=[а-яё])", "-", t)
    return t


def fix_index(text: str) -> str:
    """Индекс дисциплины УП (Б1.О.01.02, Б2.В.01(Пд)) после OCR."""
    t = (text or "").strip().replace(",", ".").replace(" ", "").strip(".-")
    if not t:
        return ""
    t = t.translate(str.maketrans("BOYHM", "ВОУНП"))
    if re.match(r"^\d\.", t):
        t = "Б" + t            # буква «Б» не распознана
    elif re.match(r"^\d[123]\.", t):
        t = "Б" + t[1:]        # «Б», прижатая к рамке, теряет вертикальную черту и читается как «2»
    elif re.match(r"^Т[ДJ]\.", t):
        t = "Ф" + t            # то же с «Ф» в «ФТД»
    elif t[0] in "6bБ5":
        t = "Б" + t[1:]
    t = re.sub(r"^Б+", "Б", t)
    t = re.sub(r"^Б[ТTIl|]\.", "Б1.", t)       # «1» в номере блока читается как «Т» или «I»
    # двузначные номера: «О1» → «01» (буква «О» отдельным разделом — обязательная часть — не трогается)
    t = re.sub(r"(?<=\.)[ОO](?=\d)|(?<=\.\d)[ОO](?=\.|\(|$)", "0", t)
    # буква части плана после «Бn.»: О (обязательная), В (вариативная), Б (базовая), ДВ, Ф
    t = re.sub(r"^(Б\d)\.0+\.", r"\1.О.", t)
    t = re.sub(r"^(Б\d)\.(?:8|8В|В8)\.", r"\1.В.", t)
    # номера разделов плана двузначные («01»), поэтому одиночные 5, 6 и «Б5» на месте буквы — это «Б»
    t = re.sub(r"^(Б\d)\.(?:[56]|Б[56]|[56]Б|65|56)\.", r"\1.Б.", t)
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
