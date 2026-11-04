"""Краткая характеристика ОП: только проверяемые поля, без сырого текста разделов."""
from __future__ import annotations

import re

MAX_CHARS = 800
BOILERPLATE = re.compile(
    r"представляет\s+собой\s+систему\s+документов|федеральн\w*\s+государственн\w*\s+(?:образовательн\w*\s+)?стандарт|"
    r"\bфгос\b|нормативно-правов|\bприказ\w*\b|сведения\s+о\s+профессорско|"
    r"^\s*(?:содержание|оглавление|общая характеристика образовательной программы)\b", re.I)
NOISE = re.compile(r"(?:\.\s*){4,}|…{2,}|(.)\1{5,}|([а-яё]{2,3})\2{4,}", re.I)


def _text(value, limit=400):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).strip(" ;")
    return value if len(value) <= limit and not NOISE.search(value) and not BOILERPLATE.search(value) else ""


def clean_summary(text: str | None) -> str:
    """Негодный результат не показывается: вместо него используется описание по фактам."""
    value = _text(text, MAX_CHARS)
    if not value or not 3 <= len(re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«])", value)) <= 5:
        return ""
    return value


def facts(data: dict) -> dict:
    """Разрешённые поля для LLM и описания без LLM. Не подставлять sections при нехватке полей."""
    out = {}
    for key in ("qualification", "duration", "focus"):
        if value := _text(data.get(key)):
            out[key] = value
    if isinstance(data.get("ze_total"), (int, float)) and 0 < data["ze_total"] <= 1000:
        out["ze_total"] = data["ze_total"]
    for key in ("areas", "task_types", "gia_forms"):
        values = data.get(key)
        if isinstance(values, list):
            clean = [_text(v, 1200 if key == "areas" else 400) for v in values]
            if clean := [v for v in clean if v][:5]:
                out[key] = clean
    return out


def fallback(data: dict, program: dict) -> str:
    d = facts(data)
    if not d:
        return ""
    sentences = []
    name = _text(program.get("profile"), 200)
    qualification = d.get("qualification", "").lower()
    degrees = {"магистр": "магистров", "бакалавр": "бакалавров", "специалист": "специалистов"}
    intro = f"Программа «{name}»" if name else "Программа"
    if qualification in degrees:
        sentences.append(f"{intro} готовит {degrees[qualification]}.")
    elif name:
        sentences.append(f"Образовательная программа «{name}».")
    volume = []
    if d.get("duration") and re.fullmatch(r"\d+(?:[.,]\d+)?\s+(?:года?|лет|месяц\w*)", d["duration"]):
        volume.append(f"обучение длится {d['duration']}")
    if d.get("ze_total"):
        volume.append(f"объём программы — {d['ze_total']:g} зачётных единиц")
    if volume:
        sentence = "; ".join(volume)
        sentences.append(sentence[:1].upper() + sentence[1:] + ".")
    if d.get("focus"):
        sentences.append(d["focus"].rstrip(".") + ".")
    elif d.get("areas"):
        # Уточнения в скобках часто занимают целую страницу. Сфера названа до скобок.
        areas = [re.sub(r"^\d{2}\.?\s*", "", a).split(" (")[0].rstrip(".;") for a in d["areas"][:2]]
        prefix = "Профессиональные области — " if len(areas) > 1 else "Профессиональная область — "
        sentences.append(prefix + "; ".join(a[:1].lower() + a[1:] for a in areas) + ".")
    activities = []
    for task in d.get("task_types", []):
        for stem, label in (("научно-исследовательск", "исследовательскую"), ("производственно-технологическ", "производственно-технологическую"),
                            ("сервисно-эксплуатацион", "сервисно-эксплуатационную"), ("технологическ", "технологическую"),
                            ("проектн", "проектную"), ("организационно-управленческ", "организационную и управленческую")):
            if task.lower().startswith(stem) and label not in activities:
                activities.append(label)
                break
    if activities:
        joined = ", ".join(activities[:-1]) + " и " + activities[-1] if len(activities) > 1 else activities[0]
        sentences.append("Подготовка ориентирована на " + joined + " работу.")
    if any("выпускной квалификационной" in f.lower() and "защит" in f.lower() for f in d.get("gia_forms", [])):
        sentences.append("Обучение завершается защитой выпускной квалификационной работы.")
    out = ""
    for sentence in sentences[:5]:
        if len(out) + len(sentence) + bool(out) <= MAX_CHARS:
            out += (" " if out else "") + sentence
    return out
