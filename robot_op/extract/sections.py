"""Документы с текстовым слоем: деление текста на разделы по заголовкам и извлечение сведений (ТЗ, шаг 6).

Для каждого типа документа задан шаблон — перечень разделов с ключевыми словами заголовков. Найдены
все обязательные разделы — документ обработан; часть разделов не найдена — «частично обработан»;
не найдено большинство — «неизвестный шаблон» (ТЗ, раздел 8).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import tables as tbl

NUM = r"^\s*(?:раздел\s+)?(?:\d{1,2}(?:\.\d{1,2})*\.?\s+)?"


@dataclass
class Spec:
    key: str
    title: str
    pattern: str
    required: bool = True


TEMPLATES: dict[str, list[Spec]] = {
    "rpd": [
        Spec("results", "Планируемые результаты обучения", r"(перечень\s+)?планируемых\s+результатов\s+обучения|компетенци\w+.*формируем"),
        Spec("place", "Место дисциплины в структуре ООП", r"место\s+дисциплины"),
        Spec("volume", "Объем дисциплины", r"объ[её]м\s+дисциплины|трудо[её]мкость\s+дисциплины"),
        Spec("content", "Содержание дисциплины", r"содержание\s+дисциплины|структура\s+и\s+содержание"),
        Spec("self_study", "Учебно-методическое обеспечение самостоятельной работы", r"(учебно-)?методическ\w+\s+обеспечени\w+\s+(для\s+)?самостоятельн", False),
        Spec("assessment", "Оценочные средства", r"фонд\s+оценочных\s+средств|оценочные\s+средства"),
        Spec("literature", "Литература", r"(основная\s+и\s+дополнительная\s+)?литератур|учебн\w+\s+литератур"),
        Spec("internet", "Ресурсы сети «Интернет»", r"ресурс\w+\s+информационно-телекоммуникационной|сети\s+[«\"]?интернет", False),
        Spec("software", "Информационные технологии и ПО", r"информационных\s+технологий|программного\s+обеспечения", False),
        Spec("facilities", "Материально-техническое обеспечение", r"материально-техническ", False),
    ],
    "practice": [
        Spec("kind", "Вид, способ и форма практики", r"вид\w*\s+практики|тип\s+практики"),
        Spec("results", "Планируемые результаты обучения", r"планируемых\s+результатов\s+обучения"),
        Spec("place", "Место практики в структуре ООП", r"место\s+практики"),
        Spec("volume", "Объем практики", r"объ[её]м\s+практики|продолжительность\s+практики"),
        Spec("content", "Содержание практики", r"содержание\s+практики|структура\s+и\s+содержание"),
        Spec("reporting", "Формы отчетности", r"форм\w+\s+отч[её]тности", False),
        Spec("assessment", "Оценочные средства", r"фонд\s+оценочных\s+средств|оценочные\s+средства"),
        Spec("literature", "Литература", r"литератур"),
        Spec("facilities", "Материально-техническая база", r"материально-техническ", False),
    ],
    "oop": [
        Spec("general", "Общая характеристика программы", r"общ\w+\s+характеристик|общие\s+положения"),
        Spec("activity", "Характеристика профессиональной деятельности", r"характеристика\s+профессиональной\s+деятельности|области\s+профессиональной"),
        Spec("results", "Планируемые результаты освоения", r"планируемые\s+результаты\s+освоения|компетенции\s+выпускник"),
        Spec("structure", "Структура и объем программы", r"структура\s+(и\s+объ[её]м\s+)?(образовательной\s+)?программы", False),
        Spec("conditions", "Условия реализации", r"условия\s+реализации", False),
    ],
    "gia": [
        Spec("general", "Общие положения", r"общие\s+положения|цел\w+\s+(и\s+задачи\s+)?государственной", False),
        Spec("forms", "Формы ГИА", r"форм\w+\s+(проведения\s+)?государственной\s+итоговой|формы\s+гиа"),
        Spec("volume", "Объем ГИА", r"объ[её]м\s+государственной|объ[её]м\s+гиа|трудо[её]мкость"),
        Spec("vkr", "Требования к ВКР", r"требования\s+к\s+выпускной|выпускн\w+\s+квалификационн\w+\s+работ"),
        Spec("procedure", "Порядок проведения", r"порядок\s+(проведения|защиты)", False),
        Spec("criteria", "Критерии оценивания", r"критери\w+\s+оценивания|критери\w+\s+оценки"),
    ],
    "fos_gia": [
        Spec("competencies", "Проверяемые компетенции", r"перечень\s+компетенций|компетенци\w+,?\s+(проверяемых|формирование)"),
        Spec("indicators", "Показатели оценивания", r"показател\w+"),
        Spec("tasks", "Типовые задания и тематика ВКР", r"типовые\s+(контрольные\s+)?задания|тематик\w+|примерн\w+\s+перечень"),
        Spec("criteria", "Критерии оценивания", r"критери\w+\s+оценивания|критери\w+\s+оценки|шкал\w+\s+оценивания"),
    ],
}

# Признаки типа на первых страницах (проверка соответствия содержимого типу ссылки, ТЗ, шаг 5)
TYPE_MARKERS = {
    "rpd": r"рабоч\w+\s+программ\w+\s+дисциплин|программ\w+\s+дисциплин|\bрпд\b",
    "practice": r"программ\w+\s+(\w+\s+){0,3}практик",
    "oop": r"основн\w+\s+(профессиональн\w+\s+)?образовательн\w+\s+программ|\bопоп\b|\bооп\b|общая\s+характеристика\s+образовательной\s+программы",
    "fos_gia": r"фонд\w*\s+оценочн\w+\s+средств",
    "gia": r"программ\w+\s+государственн\w+\s+итогов\w+\s+аттестац",
    "up": r"учебн\w+\s+план|наименовани|семестр",
    "kug": r"календарн\w+\s+учебн\w+\s+график",
}
NOT_DOCUMENT = r"\bаннотаци"


def type_check(doc_type: str, first_pages: str) -> tuple[bool, str]:
    t = re.sub(r"\s+", " ", first_pages.lower().replace("ё", "е"))
    head = t[:600]
    if re.search(NOT_DOCUMENT, head):
        return False, "вместо документа размещена аннотация"
    if doc_type == "fos_gia":
        ok = re.search(TYPE_MARKERS["fos_gia"], t) and re.search(r"итогов\w+\s+аттестац|\bгиа\b", t)
        return bool(ok), "" if ok else "не найдены признаки ФОС ГИА"
    if doc_type == "gia" and re.search(TYPE_MARKERS["fos_gia"], head):
        return False, "вместо программы ГИА размещён ФОС ГИА"
    ok = re.search(TYPE_MARKERS[doc_type], t)
    return bool(ok), "" if ok else "содержимое не соответствует типу документа"


# строка оглавления: «2.2. Требования к выпускной квалификационной работе ........ 4»
TOC_LINE = re.compile(r"(?:\.\s*){4,}|…{2,}")


def split_sections(text: str, specs: list[Spec]) -> dict[str, dict]:
    # OCR оглавления может потерять номера, точки-лидеры и границы строк.
    # Сохраняем границы страниц: страница содержания не является разделом ООП.
    pages = text.split("\f")
    if len(pages) > 1 and any(spec.key == "activity" for spec in specs):
        pages = [p for p in pages if not (re.search(r"(?im)^\s*(?:содержание|оглавление)\s*$", p)
                 and not re.search(r"(?m)^\s*\d{1,2}\.\d{1,2}\s+", p))]
    lines = [ln.strip() for ln in "\n".join(pages).splitlines()]
    found: list[tuple[int, Spec, str]] = []
    used = set()
    for i, line in enumerate(lines):
        low = line.lower().replace("ё", "е")
        if not low or len(low) > 220 or TOC_LINE.search(low):
            continue
        # заголовок: строка с номером раздела или короткая строка без точки в конце
        numbered = re.match(r"^\s*(раздел\s+)?\d{1,2}(\.\d{1,2})*\.?\s+\S", low)
        if not numbered and (len(low) > 120 or low.endswith((".", ";", ","))):
            continue
        for spec in specs:
            if spec.key in used:
                continue
            if re.match(NUM + r"(?:[\w-]+\s+){0,3}?(?:" + spec.pattern + ")", low):
                found.append((i, spec, line))
                used.add(spec.key)
                break
    found.sort(key=lambda x: x[0])
    sections = {}
    for n, (i, spec, line) in enumerate(found):
        end = found[n + 1][0] if n + 1 < len(found) else len(lines)
        body_lines = lines[i + 1:end]
        # перенос длинного заголовка на следующую строку
        heading = line
        while body_lines and body_lines[0] and body_lines[0][:1].islower() and len(heading) < 200 and not heading.endswith("."):
            heading += " " + body_lines.pop(0)
        sections[spec.key] = {"title": spec.title, "heading": heading, "text": "\n".join(l for l in body_lines if l).strip()}
    return sections


# ---------------------------------------------------------------- извлечение сведений

COMP_RE = re.compile(r"\b((?:УК|ОПК|ПК|ПКС|ОК|ПКО|ПКР)-\d{1,2})\b\s*[—–\-:.]?\s*([^;]{0,400})")
CODE_RE = re.compile(r"\b((?:УК|ОПК|ПК|ПКС|ОК|ПКО|ПКР)-\d{1,2})\b")
ZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:зачетн\w+|зач\.?)\s*(?:единиц\w*|ед\.?)|(\d+(?:[.,]\d+)?)\s*(?:з\.\s*е\.?(?:\s*т\.?)?|ЗЕТ\b)", re.I)
HOURS_RE = re.compile(r"(\d+)\s*(?:академическ\w+\s+)?час", re.I)
WEEKS_RE = re.compile(r"(\d+)\s*недел", re.I)
SEM_RE = re.compile(r"(\d{1,2}(?:\s*(?:,|и|-|–)\s*\d{1,2})*)\s*(?:-?м\s+)?семестр|семестр\w*\s*[:№]?\s*(\d{1,2}(?:\s*,\s*\d{1,2})*)", re.I)
CONTROL_RE = re.compile(r"(экзамен|зачет\s+с\s+оценкой|дифференцированн\w+\s+зачет|зачет|курсов\w+\s+проект|курсов\w+\s+работ)", re.I)


def _num(s: str | None) -> float | None:
    if not s:
        return None
    v = float(s.replace(",", "."))
    return int(v) if v.is_integer() else v


def competencies(text: str) -> list[dict]:
    out, seen = [], set()
    for code, desc in COMP_RE.findall(text):
        if code in seen:
            continue
        seen.add(code)
        desc = re.split(r"\b(?:УК|ОПК|ПК|ПКС|ОК|ПКО|ПКР)-\d|индикатор|\n\s*\d+\s", desc)[0]
        desc = re.sub(r"\s+", " ", desc).strip(" .,—–-")
        out.append({"code": code, "description": desc[:300]})
    return out


def _code_key(code: str) -> tuple:
    group, num = code.split("-")
    return (["УК", "ОПК", "ПК", "ПКС", "ПКО", "ПКР", "ОК"].index(group) if group in ("УК", "ОПК", "ПК", "ПКС", "ПКО", "ПКР", "ОК") else 9, int(num))


def numbered_items(text: str, prefix: str = r"(?:\d{1,3}[.)]|[—–-]|•)") -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(rf"^{prefix}\s*(.+)$", line)
        if m:
            items.append(m.group(1).strip())
        elif items and not re.match(r"^(тема|раздел|этап)\s+\d", line, re.I):
            items[-1] += " " + line
    return [re.sub(r"\s+", " ", i) for i in items]


def topic_items(text: str, word: str = r"тема|раздел") -> list[str]:
    out = []
    for m in re.finditer(rf"(?:^|\n)\s*(?:{word})\s*\d+[.:]?\s*([^\n]+(?:\n(?!\s*(?:{word})\s*\d)[^\n]+)*)", text, re.I):
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        out.append(t.split(". ")[0].rstrip("."))
    return out


def semesters(text: str) -> list[int]:
    out = set()
    for m in SEM_RE.finditer(text):
        for g in m.groups():
            for n in re.findall(r"\d{1,2}", g or ""):
                if 0 < int(n) <= 12:
                    out.add(int(n))
    return sorted(out)


def first(regex: re.Pattern, text: str) -> float | None:
    m = regex.search(text)
    if not m:
        return None
    return _num(next((g for g in m.groups() if g), None))


def controls(text: str) -> list[str]:
    out = []
    for m in CONTROL_RE.findall(text):
        t = re.sub(r"\s+", " ", m.lower())
        t = "зачет с оценкой" if "оценк" in t or "дифференц" in t else t
        t = "курсовой проект" if t.startswith("курсов") and "проект" in t else ("курсовая работа" if t.startswith("курсов") else t)
        if t not in out:
            out.append(t)
    return out


def quoted_title(text: str, word: str) -> str:
    """Название в кавычках после слова; на титуле оно часто набрано прописными — тогда берётся упоминание
    в тексте («Дисциплина «Основы научных исследований» обеспечивает…») или прописные заменяются строчными."""
    found = [re.sub(r"\s+", " ", m.group(1)).strip() for m in re.finditer(word + r"[^«\"]{0,40}[«\"]([^»\"]{3,200})[»\"]", text, re.I)]
    if not found:
        return ""
    normal = next((t for t in found if not t.isupper()), None)
    return normal or found[0][:1] + found[0][1:].lower()


def extract(doc_type: str, text: str, tables: list | None = None) -> dict:
    """tables — таблицы текстового слоя (pdf.page_tables); сведения из таблиц точнее, чем из текста."""
    specs = TEMPLATES[doc_type]
    sec = split_sections(text, specs)
    t = tbl.group(tables or [])
    body = lambda key: sec.get(key, {}).get("text", "")
    data: dict = {"sections": {k: {"title": v["title"], "text": v["text"][:4000]} for k, v in sec.items()}}
    if doc_type == "rpd":
        vol = tbl.volume(t["volume"]) if "volume" in t else {}
        data.update({
            "discipline": quoted_title(text[:6000], r"дисциплин\w*"),
            "goal": _goal(text),
            "competencies": tbl.competencies(t["competencies"]) if "competencies" in t else competencies(body("results") or text),
            "results": tbl.results(t["indicators"]) if "indicators" in t else [],
            "ze": first(ZE_RE, body("volume") or text),
            "hours": vol.get("hours") or ({"total": h} if (h := first(HOURS_RE, body("volume"))) else {}),
            "semesters": vol.get("semesters") or semesters(body("place") + "\n" + body("volume")),
            "control": vol.get("control") or [c.capitalize() for c in controls(body("volume"))],
            "topics": tbl.titled(t["topics"]) if "topics" in t else
                      [{"title": x, "summary": ""} for x in (topic_items(body("content")) or numbered_items(body("content")))[:30]],
            "labs": tbl.titled(t["labs"], hours=True) if "labs" in t else [],
            "practicals": tbl.titled(t["practicals"], hours=True) if "practicals" in t else [],
            "self_study": tbl.titled(t["self_study"], hours=True) if "self_study" in t else [],
            "literature": numbered_items(body("literature"))[:30],
        })
    elif doc_type == "practice":
        k = body("kind")
        period = tbl.practice_period(t["practice_period"]) if "practice_period" in t else {}
        data.update({
            "practice_kind": _after(k, r"вид\w*\s+практики\s*[:—–-]\s*"),
            "practice_type": _after(k, r"тип\w*\s+практики\s*[:—–-]\s*"),
            "method": _after(k, r"способ\w*\s+проведения\s*[:—–-]\s*"),
            "competencies": tbl.competencies(t["competencies"]) if "competencies" in t else competencies(body("results") or text),
            "results": tbl.results(t["indicators"]) if "indicators" in t else [],
            "semesters": period.get("semesters") or semesters(body("place") + "\n" + body("volume")),
            "ze": period.get("ze") or first(ZE_RE, body("volume") or text),
            "weeks": period.get("weeks") or first(WEEKS_RE, body("volume") or text),
            "stages": (tbl.titled(t["stages"]) if "stages" in t else []) or
                      [{"title": x, "summary": ""} for x in topic_items(body("content"), r"этап|раздел") if len(x) < 200][:20],
            "content": _lead(body("content")),
            "reporting": [x for x in numbered_items(body("reporting"), r"(?:\d{1,2}[.)]|[—–\-•\uf0b7])") if len(x) < 200][:10],
            "control": period.get("control") or [c.capitalize() for c in controls(body("reporting") + "\n" + body("assessment"))][:1],
            "literature": numbered_items(body("literature"))[:20],
        })
    elif doc_type == "oop":
        act = body("activity")
        data.update({
            "characteristic": body("general")[:2500],
            "qualification": _after(text, r"квалификаци\w+,?\s*(?:присваиваем\w+\s+)?(?:выпускник\w+)?\s*[:—–-]\s*"),
            "duration": _oop_duration(text),
            "ze_total": first(ZE_RE, re.sub(r"_+", " ", body("general") or text)),
            "areas": _oop_areas(act),
            "task_types": _oop_tasks(act),
            "gia_forms": _oop_gia(body("general")),
            "competencies": competencies(body("results")),
            "conditions": body("conditions")[:1000],
        })
    elif doc_type == "gia":
        data.update({
            "forms": numbered_items(body("forms")) or [s.strip() for s in body("forms").split("\n") if s.strip()],
            "ze": first(ZE_RE, body("volume")),
            "weeks": first(WEEKS_RE, body("volume")),
            "vkr_requirements": body("vkr")[:2000],
            "vkr_topics": tbl.vkr_topics(t["vkr_topics"]) if "vkr_topics" in t else [],
            "procedure": body("procedure")[:1500],
            "criteria": body("criteria")[:1500],
        })
    elif doc_type == "fos_gia":
        data.update({
            "competencies": sorted(set(CODE_RE.findall(body("competencies") or text)), key=_code_key),
            "indicators": body("indicators")[:1500],
            "tasks": [x for x in numbered_items(body("tasks")) if 10 < len(x) < 250 and "компетенц" not in x.lower()][:40],
            "criteria": body("criteria")[:1500],
        })
    found = [s.key for s in specs if s.key in sec]
    missing_required = [s.title for s in specs if s.required and s.key not in sec]
    required_total = sum(1 for s in specs if s.required)
    data["template"] = {
        "found": found,
        "missing_required": missing_required,
        "score": round((required_total - len(missing_required)) / required_total, 2) if required_total else 1.0,
    }
    return data


def _oop_duration(text: str) -> str:
    m = re.search(r"срок\s+(?:получения\s+образования|обучения|освоения)[^\n]{0,40}?"
                  r"(\d+(?:[.,]\d+)?\s+(?:года?|лет|месяц\w*))", text, re.I)
    return m.group(1) if m else ""


def _oop_areas(text: str) -> list[str]:
    # Строки 06/16/40 — коды областей; код вида 06.017 относится уже к профстандарту.
    block = re.split(r"(?m)^\s*2\.2\b", text)[0]
    found = re.findall(r"(?:^|\n)\s*[—–\-•]?\s*(\d{2}\.?\s+[^\n]+(?:\n(?!\s*(?:[—–\-•]|\d{2}\.?\s|Выпускники))[^\n]+)*)", block)
    if not found:
        return _list_after(text, r"област\w+\s+профессиональной\s+деятельности\s*[:—–-]\s*")
    return [re.sub(r"\s+", " ", a).strip(" .;") for a in found
            if not re.match(r"\d{2}\.?\s+област", a, re.I)][:5]


def _oop_tasks(text: str) -> list[str]:
    m = re.search(r"задач\s+профессиональной\s+деятельности[^:\n]*:\s*([^\f]+)", text, re.I)
    if not m:
        return []
    block = re.split(r"(?m)^\s*\d{1,2}\.\d{1,2}\s", m.group(1))[0]
    values = [v.strip(" .;—–-•\"|") for v in re.split(r"[;\n]", block)]
    return [v for v in values if re.fullmatch(r"[а-яёА-ЯЁ-]{5,80}", v)][:5]


def _oop_gia(text: str) -> list[str]:
    m = re.search(r"форма\s+государственной\s+итоговой\s+аттестации\s*:\s*([^\n]+(?:\n(?!\s*(?:[А-ЯЁ][а-яё]+\s*[:,]|\d+\.))[^\n]+)?)", text, re.I)
    if not m:
        return []
    value = re.sub(r"_+", " ", m.group(1))
    value = re.sub(r"\s+", " ", value).strip(" .")
    return [value] if len(value) < 250 else []


def _goal(text: str) -> str:
    """«Целью освоения дисциплины является …» / «Цель дисциплины — …»; без такой фразы цель не заполняется."""
    m = re.search(r"цел(?:ь|ью)\s+(?:освоения\s+|изучения\s+|преподавания\s+)?дисциплины\s*(?:является|[—–:-])\s*([^\n]+(?:\n[^\n]+){0,3})",
                  text, re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).split(". ")[0].strip(" .")[:400]


def _lead(text: str, limit: int = 500) -> str:
    """Первый абзац раздела до таблицы или перечня («Содержание этапов приведено в таблице…»)."""
    lead = re.split(r"\n\s*(?:№\s*п/п|содержание\s+этапов|\d+\.\d|\d+\s+[А-ЯЁ][а-яё]+\s+[а-яё]+\s+по\s)", text, maxsplit=1, flags=re.I)[0]
    lead = re.sub(r"\s+", " ", lead).strip()
    return lead[:limit].rsplit(". ", 1)[0] + "." if len(lead) > limit else lead


def _after(text: str, pattern: str) -> str:
    m = re.search(pattern + r"([^\n.;]{2,200})", text, re.I)
    return m.group(m.lastindex).strip() if m else ""


def _list_after(text: str, pattern: str) -> list[str]:
    m = re.search(pattern + r"([^\n]+(?:\n(?![А-ЯЁ][а-яё]+\s+задач)[^\n]+)*)", text, re.I)
    if not m:
        return []
    raw = re.sub(r"\s+", " ", m.group(m.lastindex)).split(". ")[0]
    if ";" in raw:
        parts = raw.split(";")
    elif re.match(r"\d{2}\s", raw):
        parts = re.split(r",\s*(?=\d{2}\s)", raw)      # «06 Связь, информационные … технологии, 40 …»
    else:
        parts = raw.split(",")
    return [x.strip(" .") for x in parts if x.strip(" .")]
