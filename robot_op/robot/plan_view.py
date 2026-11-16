"""Учебный план для людей: дисциплины и нагрузка по семестрам (карусель) и доли категорий в объёме программы.

Строится по итоговому JSON-файлу УП. Категория дисциплины определяется по названию набором правил: правила
проверяются по порядку, первое совпадение побеждает. Дисциплины, не попавшие ни в одну тематическую категорию,
относятся к профессиональным — у каждого направления они свои («Водопроводные сети», «Архитектура зданий»).
"""
from __future__ import annotations

import re

from ..extract import curriculum
from ..extract.util import discipline_name, norm

# ключ → название, сокращение для значка; порядок — порядок полок на странице
CATEGORIES = {
    "prof": ("Профессиональные дисциплины", "Проф"),
    "it": ("Программирование и ИТ", "ИТ"),
    "ai": ("ИИ и анализ данных", "ИИ"),
    "math": ("Математика и естественные науки", "Мат"),
    "hum": ("Языки, гуманитарные и экономика", "Гум"),
    "prac": ("Практика, проекты и ВКР", "Пр"),
    "sport": ("Физическая культура и спорт", "Спорт"),
}

# (категория, фрагменты названия); частные случаи стоят раньше общих. Фрагменты подобраны так, чтобы не
# совпадать внутри чужих слов: «право», а не «прав» (уПРАВление); «мышление», а не «мышлен» (проМЫШЛЕННых)
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("sport", ("физическая культура", "физической культур", "спорт", "гимнастик", "бокс", "фитнес",
               "физическая подготовка", "физической подготовк")),
    ("prac", ("практика", "практики", "проектная деятельность", "проектной деятельности",
              "выпускной квалификационной", "квалификационного экзамена", "государственного экзамена",
              "научно-исследовательская работа")),
    ("hum", ("интеллектуальной собственности", "интеллектуальной деятельности", "авторское право")),
    ("ai", ("искусственн", "машинн", "нейрон", "глубокое обучение", "компьютерного зрения", "языковые модели",
            "естественного языка", "анализ данных", "анализа данных", "визуализац", "больших данных",
            "наука о данных", "распознаван", "робототехн", " ml", "ml ")),
    ("it", ("баз данных", "базы данных", "компьютерн", "телекоммуникац", "программирован", "программ-робот",
            "программного обеспечения", "программное обеспечение")),
    ("ai", ("оптимизац", "исследование операций", "моделирован", "автоматического управления")),
    ("math", ("математ", "физика", "химия", "механика", "механики", "графика", "геометр", "статистик",
              "вероятност", "алгоритм", "логик", "электротехник", "сопротивление материалов", "геодези",
              "геолог", "гидравлик", "экологи", "биологи")),
    ("hum", ("язык", "истори", "философ", "право", "эконом", "деловой коммуникац", "психолог", "социолог",
             "культур", "государственност", "жизнедеятельности", "мышление", "инклюзив", "коммерциализац",
             "менеджмент", "ит-проект", "лингвист", "русский", "риторик", "форсайт", "письмо", "патент")),
    ("it", ("информатик", "информацион", "операционн", "эвм", "электроник", "интернет", "web", "веб", "1с",
            "1c", "асои", "скрипт", "python", "разработк", "цифров", "кибер", "защита информации", "сети и ",
            "блокчейн", "распределенн")),
]


def categorize(name: str) -> str:
    t = " " + (name or "").lower().replace("ё", "е") + " "
    for key, parts in RULES:
        if any(p in t for p in parts):
            return key
    return "prof"


_CHOICE = re.compile(r"\.ДВ\.\d\d$")
_TRACK_GROUP = re.compile(r"(?:(?:индивидуальн\w+ )?образовательн\w+ (?:траектори\w+|трек\w+)|треки(?: обучения)?)[.:]?")


def is_choice(index: str) -> bool:
    """Блок дисциплин по выбору; в УП он может быть помечен как модуль."""
    return bool(_CHOICE.search(index))


def is_choice_option(index: str) -> bool:
    return is_choice(index.rpartition(".")[0])


def track_rows(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Траектории выделены отдельными модулями внутри именованного блока ДВ."""
    children: dict[str, list[dict]] = {}
    for row in rows:
        children.setdefault(row.get("index", "").rpartition(".")[0], []).append(row)
    groups = {}
    for row in rows:
        index = row.get("index", "")
        if not is_choice(index) or not _TRACK_GROUP.fullmatch(norm(row["name"])):
            continue
        branches = {norm(branch["name"]): branch for branch in children.get(index, [])
                    if branch["index"] in children}
        if branches:
            groups[index] = branches
    if not groups:
        return []
    # Одни траектории могут повторяться в блоках дисциплин и практик с другим порядком.
    names = max(groups.values(), key=len)
    groups = {index: group for index, group in groups.items() if group.keys() <= names.keys()}
    out = []
    for name, branch in names.items():
        prefixes = tuple(group[name]["index"] + "." for group in groups.values() if name in group)
        selected = [row for row in rows if row.get("index", "").startswith(prefixes)
                    or not any(row.get("index", "") == index or row.get("index", "").startswith(index + ".")
                               for index in groups)]
        out.append((discipline_name(branch["name"]), selected))
    return out


def _items(rows: list[dict]) -> list[dict]:
    """Одна строка на блок по выбору, с нагрузкой блока и названиями его вариантов."""
    options_by_parent: dict[str, list[dict]] = {}
    for row in rows:
        idx = row.get("index", "")
        if is_choice_option(idx):
            options_by_parent.setdefault(idx.rpartition(".")[0], []).append(row)
    out = []
    for r in rows:
        idx = r.get("index", "")
        if is_choice_option(idx):
            continue
        if is_choice(idx):
            options = options_by_parent.get(idx, [])
            names = [discipline_name(option["name"]) for option in options]
            title = " / ".join(names) or r["name"]
            category = _choice_category([categorize(n) for n in names] or [categorize(r["name"])])
            out.append(_item(r, title, category, options=options))
        elif not r.get("is_module"):
            out.append(_item(r, r["name"], categorize(r["name"])))
    return out


def _choice_category(cats: list[str]) -> str:
    """Категория блока по выбору: самая частая среди вариантов; тематическая важнее «профессиональной»,
    при равенстве — категория первого варианта."""
    pool = [c for c in cats if c != "prof"] or cats
    return max(pool, key=lambda c: (pool.count(c), -pool.index(c)))


def semester_numbers(row: dict) -> set[int]:
    return {int(s) for s in row.get("semesters", {})} | {s for v in row.get("control", {}).values() for s in v}


def _item(r: dict, name: str, cat: str, options: list[dict] | None = None) -> dict:
    return {"name": discipline_name(name), "category": cat, "ze": r.get("ze") or 0,
            "semesters": semester_numbers(r), "control": r.get("control", {}), "index": r.get("index", ""),
            "elective": options is not None, "optional": r.get("index", "").startswith("ФТД"), "options": options or [],
            "practice": ("(" in r.get("index", "") and r.get("index", "")[:2] in ("Б2", "Б3")) or r.get("index", "").startswith(("УП.", "ПП.")),
            "hours": r.get("semesters", {}), "match": r["name"]}


def _pairs(hours: float) -> float:
    """Пара — два академических часа."""
    return round((hours or 0) / 2, 1)


def _control(control: dict, sem: int) -> str:
    return curriculum.control_label(control, sem)


def build(up_data: dict, links: dict[str, str] | None = None, key=lambda s: s) -> dict | None:
    """Контекст карусели и полок учебного плана, включая общие и трековые дисциплины."""
    rows = up_data.get("rows") or []
    if not rows:
        return None
    links = links or {}
    weeks = {s["number"]: s["weeks"] for s in up_data.get("semesters", [])}
    n_sem = max(max(weeks, default=0), max((s for row in rows for s in semester_numbers(row)), default=0))
    variants = track_rows(rows)
    if len(variants) <= 1:
        selected = variants[0][1] if variants else rows
        return dict(_build_plan(selected, weeks, n_sem, links, key), tracks=[])
    tracks = [dict(_build_plan(selected, weeks, n_sem, links, key), name=name) for name, selected in variants]
    categories = {c["key"]: c for track in tracks for c in track["categories"]}
    return dict(tracks[0], tracks=tracks, categories=[categories[k] for k in CATEGORIES if k in categories])


def _build_plan(rows: list[dict], weeks: dict, n_sem: int, links: dict, key) -> dict:
    items = _items(rows)
    semesters = []
    for s in range(1, n_sem + 1):
        here = sorted((it for it in items if s in it["semesters"]),
                      key=lambda i: (i["practice"], i["optional"], list(CATEGORIES).index(i["category"]), i["name"]))
        entries = []
        sem_total = {"lec": 0.0, "pr": 0.0, "lab": 0.0}
        for it in here:
            h = it["hours"].get(str(s)) or {}
            load = {k: _pairs(h.get(k)) for k in ("lec", "pr", "lab")}
            entries.append({"name": it["name"], "category": it["category"], "control": _control(it["control"], s),
                            "url": links.get(it["index"]) or links.get(key(it["match"])),
                            "options": [{"name": discipline_name(row["name"]),
                                         "url": links.get(row.get("index")) or links.get(key(row["name"]))}
                                        for row in it["options"]],
                            "elective": it["elective"], "optional": it["optional"],
                            "practice": it["practice"], "ze": it["ze"], **load, "pairs": round(sum(load.values()), 1)})
            if not it["optional"]:
                for k in sem_total:
                    sem_total[k] += load[k]
        pairs = round(sum(sem_total.values()), 1)
        w = weeks.get(s)
        semesters.append({"number": s, "course": (s + 1) // 2, "weeks": w, "items": entries,
                          **{k: round(v, 1) for k, v in sem_total.items()}, "pairs": pairs,
                          "per_week": round(pairs / w, 1) if w else None,
                          "hours": pairs * 1.5,
                          "hours_per_week": pairs * 1.5 / w if w else None,
                          "count": sum(1 for e in entries if not e["practice"] and not e["optional"]),
                          "exams": sum(1 for e in entries if "Экз" in e["control"])})

    credit_based = any(it["ze"] for it in items)
    weight = lambda it: it["ze"] if credit_based else sum(h.get("total", sum(h.get(k, 0) for k in ("lec", "pr", "lab", "sr", "control"))) for h in it["hours"].values())
    total = sum(weight(it) for it in items if not it["optional"]) or 1
    shelves = []
    for k, (title, short) in CATEGORIES.items():
        group = [it for it in items if it["category"] == k]
        if not group:
            continue
        ze = sum(weight(it) for it in group if not it["optional"])
        shelves.append({
            "key": k, "title": title, "short": short, "count": len(group), "ze": ze,
            "share": round(ze / total * 100),
        })
    return {"semesters": semesters, "shelves": shelves, "total_ze": total, "unit": "з. е." if credit_based else "акад. ч",
            "categories": [{"key": k, "title": t, "short": s} for k, (t, s) in CATEGORIES.items()
                           if any(sh["key"] == k for sh in shelves)]}
