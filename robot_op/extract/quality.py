"""Проверки структурного мусора без переписывания содержания и удаления значащих чисел.

Это проверка известных дефектов, а не доказательство смысловой правильности произвольного PDF.
Оригинальные sections остаются в первичном JSON; отклонённые поля отражаются в warnings.
"""
from __future__ import annotations

from copy import deepcopy
import re

QUALIFIED_LITERATURE = (r"(?:основная|дополнительная)(?:\s+и\s+дополнительная)?\s+"
                        r"(?:учебная\s+(?:и\s+справочная\s+)?)?литература(?:\s+и\s+справочная)?")
LITERATURE_HEADER = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:" + QUALIFIED_LITERATURE + r"|литература)\s*[:.]?\s*$", re.I)
NUMERIC_GRID = re.compile(r"\b(?:\d{1,2}[,.]?\s+){4,}|\b\d{1,2}\s+\d{1,2}\.\d{1,2}\.")

ADMIN = (r"перечень\s+(?:лабораторн\w*\s+работ|практическ\w*\s+занятий)|"
         r"методическ\w+\s+указания\s+для\s+обучающихся\s+по\s+(?:освоению|лабораторным|самостоятельной)|"
         r"сводные\s+данные\s+по\s+содержанию|"
         + QUALIFIED_LITERATURE + r"|ресурсы\s+сети\s+интернет|"
         r"перечень\s+учебно-методического\s+обеспечения")
ADMIN_TITLE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:" + ADMIN + r")", re.I)
ADMIN_SUFFIX = re.compile(r"\s+\d{1,2}(?:\.\d+)*\.?\s+(?:" + ADMIN + r")", re.I)


def learning_results(items: list) -> list[str]:
    """Не считать подпись категории отдельным результатом обучения."""
    out = []
    for item in items or []:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split()).strip(" .;")
        value = re.sub(r"^(Знать|Уметь|Владеть)(?:\s*:\s*|\s+)\1\b", r"\1", value, flags=re.I)
        if value and not re.fullmatch(r"(?:Знать|Уметь|Владеть)\s*[:—–-]?", value, re.I) and value not in out:
            out.append(value)
    return out


def qualification(value: str) -> str:
    """Отсеять известную шапку таблицы вместо присваиваемой квалификации."""
    value = " ".join((value or "").split()).strip(' .;«»"')
    if re.search(r"наименование\s+(?:видов|профессиональных)|присваиваемой\s+квалификации", value, re.I):
        return ""
    return value


def validate(doc_type: str, data: dict) -> dict:
    """Вернуть копию с проверенными полями; предупреждения сохраняются между повторными проверками."""
    d = deepcopy(data)
    extraction = d.setdefault("extraction", {})
    extraction.setdefault("sources", {})
    warnings = extraction.setdefault("warnings", [])

    def warn(field, reason):
        value = {"field": field, "reason": reason}
        if value not in warnings:
            warnings.append(value)

    if doc_type == "oop" and d.get("qualification"):
        if not (value := qualification(d["qualification"])):
            warn("qualification", "Шапка таблицы вместо квалификации")
        d["qualification"] = value
    if doc_type in ("rpd", "practice") and "results" in d:
        d["results"] = learning_results(d["results"])
    goal = d.get("goal")
    if goal and (ADMIN_TITLE.search(goal) or re.fullmatch(r"[а-яё]+\s+\d{1,3}", goal, re.I)):
        d["goal"] = ""
        warn("goal", "Обрывок текста вместо цели")
    for field in ("topics", "stages", "labs", "practicals", "self_study"):
        if field not in d:
            continue
        kept = []
        for item in d[field] or []:
            title = item.get("title", "") if isinstance(item, dict) else str(item)
            if NUMERIC_GRID.search(title):
                warn(field, "Подозрительная числовая вставка внутри названия")
                continue
            if ADMIN_TITLE.search(title) or not re.search(r"[А-ЯЁа-яёA-Za-z]", title):
                warn(field, "Заголовок соседнего раздела вместо элемента")
                continue
            if isinstance(item, dict):
                for key in ("title", "summary"):
                    value = item.get(key) or ""
                    if marker := ADMIN_SUFFIX.search(value):
                        item[key] = value[:marker.start()].rstrip()
                        warn(field, "Захвачен заголовок соседнего раздела")
            kept.append(item)
        d[field] = kept
    if doc_type in ("rpd", "practice"):
        for comp in d.get("competencies") or []:
            if not isinstance(comp, dict):
                continue
            text = comp.get("description") or ""
            if text and (re.match(r"^\d+[ .]", text) or re.search(r"\b(?:и|или|в|с|со|для|по|на|от|из|к|о)\s*$", text, re.I)):
                comp["description"] = ""
                warn("competencies", "Описание компетенции обрезано или смешано с другой колонкой")
        if "literature" in d:
            items = []
            for item in d["literature"] or []:
                if ADMIN_TITLE.search(item):
                    warn("literature", "Служебный заголовок вместо источника")
                    continue
                if marker := ADMIN_SUFFIX.search(item):
                    item = item[:marker.start()].rstrip()
                    warn("literature", "К источнику приклеен заголовок следующего раздела")
                if item:
                    items.append(item)
            d["literature"] = items
    return d
