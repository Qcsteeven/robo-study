"""Страница документа по итоговому JSON-файлу: для каждого типа — фиксированная схема вывода.

Показываются только структурированные поля (сведения, списки, таблицы); сплошной текст разделов документа
на страницу не выводится. Старые JSON-файлы (до разбора таблиц) показываются по тем же полям: длинные
строки, в которые при извлечении склеилась таблица, отбрасываются.
"""
from __future__ import annotations

import re

MAX_ITEM = 400      # длиннее — склеенный текст таблицы, а не пункт списка


def _num(v) -> str:
    return ("%g" % v).replace(".", ",") if isinstance(v, (int, float)) else str(v)


def _plural(n, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _hours(v) -> str:
    return f"{_num(v)} ч" if v else ""


def _sems(v) -> str:
    return ", ".join(str(s) for s in sorted(set(v))) if v else ""


def _comps(items) -> list[str]:
    out = []
    for c in items or []:
        if isinstance(c, dict):
            text = f"{c['code']} — {c['description']}" if c.get("description") else c.get("code", "")
        else:
            text = str(c)
        if text and len(text) <= MAX_ITEM:
            out.append(text)
    return out


def _texts(items, limit: int = 30) -> list[str]:
    return [re.sub(r"\s+", " ", str(x)).strip() for x in (items or []) if x and len(str(x)) <= MAX_ITEM][:limit]


def _titled(items) -> list[dict]:
    """[{title, summary}] — новые JSON; строки старых JSON становятся темами без описания."""
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get("title"):
            out.append({"title": it["title"], "summary": it.get("summary") or ""})
        elif isinstance(it, str) and len(it) <= 200:
            out.append({"title": it, "summary": ""})
    return out


def _hours_rows(items) -> list[dict]:
    return [{"title": it["title"], "hours": _hours(it.get("hours"))} for it in items or [] if isinstance(it, dict) and it.get("title")]


def _volume(d: dict) -> list[tuple[str, str]]:
    h = d.get("hours") if isinstance(d.get("hours"), dict) else ({"total": d["hours"]} if d.get("hours") else {})
    facts = []
    if d.get("ze") or h.get("total"):
        facts.append(("Трудоёмкость", " · ".join(x for x in (f"{_num(d['ze'])} з. е." if d.get("ze") else "",
                                                               _hours(h.get("total"))) if x)))
    parts = [f"{label} {_num(h[k])} ч" for k, label in (("lec", "лекции"), ("pr", "практические"), ("lab", "лабораторные"))
             if h.get(k)]
    if parts:
        facts.append(("Аудиторные занятия", ", ".join(parts)))
    if h.get("sr"):
        facts.append(("Самостоятельная работа", _hours(h["sr"])))
    return facts


def build(doc_type: str, d: dict) -> dict:
    """{facts: [(название, значение)], blocks: [{title, kind: list|topics|hours, items}]}"""
    facts: list[tuple[str, str]] = []
    blocks: list[dict] = []

    def block(title: str, kind: str, items):
        if items:
            blocks.append({"title": title, "kind": kind, "items": items})

    if doc_type == "rpd":
        facts = [("Семестр" if len(d.get("semesters") or []) == 1 else "Семестры", _sems(d.get("semesters"))),
                 *_volume(d),
                 ("Промежуточная аттестация", ", ".join(c[:1].upper() + c[1:] for c in d.get("control") or []))]
        if d.get("goal"):
            facts.insert(0, ("Цель", d["goal"]))
        block("Темы и содержание", "topics", _titled(d.get("topics")))
        block("Лабораторные работы", "hours", _hours_rows(d.get("labs")))
        block("Практические занятия", "hours", _hours_rows(d.get("practicals")))
        block("Самостоятельная работа", "hours", _hours_rows(d.get("self_study")))
        block("Формируемые компетенции", "list", _comps(d.get("competencies")))
        block("Результаты обучения", "list", _texts(d.get("results")))
        block("Литература", "list", _texts(d.get("literature"), 15))
    elif doc_type == "practice":
        vol = " · ".join(x for x in (f"{_num(d['ze'])} з. е." if d.get("ze") else "",
                                      f"{_num(d['weeks'])} недел{_plural(d['weeks'], 'я', 'и', 'ь')}" if d.get("weeks") else "") if x)
        facts = [("Вид практики", d.get("practice_kind")), ("Тип практики", d.get("practice_type")),
                 ("Способ проведения", d.get("method")), ("Семестр", _sems(d.get("semesters"))), ("Объём", vol),
                 ("Промежуточная аттестация", ", ".join(d.get("control") or []))]
        block("Этапы практики", "topics", _titled(d.get("stages")))
        if not d.get("stages") and d.get("content"):
            block("Содержание практики", "list", [d["content"]])
        block("Отчётные документы", "list", _texts(d.get("reporting") if isinstance(d.get("reporting"), list) else [], 10))
        block("Формируемые компетенции", "list", _comps(d.get("competencies")))
        block("Результаты обучения", "list", _texts(d.get("results")))
        block("Литература", "list", _texts(d.get("literature"), 15))
    elif doc_type == "oop":
        facts = [("Квалификация", d.get("qualification")), ("Срок обучения", d.get("duration")),
                 ("Объём программы", f"{_num(d['ze_total'])} з. е." if d.get("ze_total") else "")]
        block("Области профессиональной деятельности", "list", _texts(d.get("areas")))
        block("Типы задач", "list", _texts(d.get("task_types")))
        block("Компетенции выпускника", "list", _comps(d.get("competencies")))
    elif doc_type == "gia":
        facts = [("Формы ГИА", "; ".join(f.rstrip(".") for f in _texts(d.get("forms"), 5))),
                 ("Объём", f"{_num(d['ze'])} з. е." if d.get("ze") else ""),
                 ("Продолжительность", f"{_num(d['weeks'])} нед." if d.get("weeks") else "")]
        block("Примерные темы ВКР", "list", _texts(d.get("vkr_topics"), 20))
    elif doc_type == "fos_gia":
        facts = [("Проверяемые компетенции", ", ".join(d.get("competencies") or []))]
    elif doc_type == "up" and d.get("semesters"):
        facts = [("Семестров", str(len(d["semesters"])))]
    elif doc_type == "kug" and d.get("courses"):
        facts = [("Учебный год", d.get("academic_year")), ("Курсов", str(len(d["courses"])))]
    return {"facts": [(k, v) for k, v in facts if v], "blocks": blocks}
