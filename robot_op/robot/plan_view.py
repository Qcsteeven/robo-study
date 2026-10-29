"""Учебный план для людей: дисциплины и нагрузка по семестрам (карусель) и доли категорий в объёме программы.

Строится по итоговому JSON-файлу УП. Категория дисциплины определяется по названию набором правил: правила
проверяются по порядку, первое совпадение побеждает. Дисциплины, не попавшие ни в одну тематическую категорию,
относятся к профессиональным — у каждого направления они свои («Водопроводные сети», «Архитектура зданий»).
"""
from __future__ import annotations

import re

from ..extract import curriculum

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
_CHOICE_ITEM = re.compile(r"\.ДВ\.\d\d\.\d\d$")


def _items(rows: list[dict]) -> list[dict]:
    """Строки УП → дисциплины. Модули пропускаются; блок дисциплин по выбору становится одной позицией
    «A или B» (или «… — N вариантов на выбор»), его варианты отдельно не показываются."""
    by_index = {r["index"]: r for r in rows if r.get("index")}
    out = []
    for r in rows:
        idx = r.get("index", "")
        if _CHOICE_ITEM.search(idx):
            continue
        if _CHOICE.search(idx):
            options = [x for x in rows if x.get("index", "").startswith(idx + ".")]
            names = [o["name"] for o in options]
            if len(names) > 3:
                parent = by_index.get(idx.rsplit(".ДВ.", 1)[0])
                title = f"{parent['name'] if parent else r['name']}: {len(names)} вариантов на выбор"
            elif names:
                title = "На выбор: " + " или ".join(n[:1].lower() + n[1:] if i else n for i, n in enumerate(names))
            else:
                title = r["name"]
            out.append(_item(r, title, _choice_category([categorize(n) for n in names] or [categorize(r["name"])]),
                             options=names))
            continue
        if r.get("is_module"):
            continue
        out.append(_item(r, r["name"], categorize(r["name"])))
    return out


def _choice_category(cats: list[str]) -> str:
    """Категория блока по выбору: самая частая среди вариантов; тематическая важнее «профессиональной»,
    при равенстве — категория первого варианта."""
    pool = [c for c in cats if c != "prof"] or cats
    return max(pool, key=lambda c: (pool.count(c), -pool.index(c)))


def _item(r: dict, name: str, cat: str, options: list[str] | None = None) -> dict:
    sems = sorted(set(int(s) for s in r.get("semesters", {})) | {s for v in r.get("control", {}).values() for s in v})
    return {"name": name, "category": cat, "ze": r.get("ze") or 0, "semesters": sems, "control": r.get("control", {}),
            "elective": options is not None, "optional": r.get("index", "").startswith("ФТД"), "options": options or [],
            "practice": "(" in r.get("index", "") and r.get("index", "")[:2] in ("Б2", "Б3"),
            "hours": r.get("semesters", {}), "match": r["name"]}


def _pairs(hours: float) -> float:
    """Пара — два академических часа."""
    return round((hours or 0) / 2, 1)


def _control(control: dict, sem: int) -> str:
    return curriculum.control_label(control, sem)


def build(up_data: dict, links: dict[str, str] | None = None, key=lambda s: s) -> dict | None:
    """Контекст карусели и полок. links: ключ названия (key(name)) → адрес страницы документа."""
    rows = up_data.get("rows") or []
    if not rows:
        return None
    links = links or {}
    weeks = {s["number"]: s["weeks"] for s in up_data.get("semesters", [])}
    items = _items(rows)
    for it in items:
        it["url"] = links.get(key(it["match"])) or next((links[key(o)] for o in it["options"] if key(o) in links), None)

    n_sem = max(weeks) if weeks else max((s for it in items for s in it["semesters"]), default=0)
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
                            "url": it["url"], "elective": it["elective"], "optional": it["optional"],
                            "practice": it["practice"], "ze": it["ze"], **load, "pairs": round(sum(load.values()), 1)})
            if not it["optional"]:
                for k in sem_total:
                    sem_total[k] += load[k]
        pairs = round(sum(sem_total.values()), 1)
        w = weeks.get(s)
        semesters.append({"number": s, "course": (s + 1) // 2, "weeks": w, "items": entries,
                          **{k: round(v, 1) for k, v in sem_total.items()}, "pairs": pairs,
                          "per_week": round(pairs / w, 1) if w else None,
                          "count": sum(1 for e in entries if not e["practice"] and not e["optional"]),
                          "exams": sum(1 for e in entries if "Экз" in e["control"])})

    total = sum(it["ze"] for it in items if not it["optional"]) or 1
    shelves = []
    for k, (title, short) in CATEGORIES.items():
        group = [it for it in items if it["category"] == k]
        if not group:
            continue
        ze = sum(it["ze"] for it in group if not it["optional"])
        shelves.append({
            "key": k, "title": title, "short": short, "count": len(group), "ze": ze,
            "share": round(ze / total * 100),
        })
    return {"semesters": semesters, "shelves": shelves, "total_ze": total,
            "categories": [{"key": k, "title": t, "short": s} for k, (t, s) in CATEGORIES.items()
                           if any(sh["key"] == k for sh in shelves)]}
