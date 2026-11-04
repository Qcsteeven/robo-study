"""Паспорт и тематический план практики СПО с разделами профессионального модуля."""
from __future__ import annotations
import re
from .util import unwrap_text


def extract(text: str, tables: list) -> dict | None:
    table_rows = []
    active = False
    for page, rows in tables:
        head = " ".join(rows[0]) if rows else ""
        if re.search(r"наименован.*разделов.*тем", head, re.I) and "Содержание" in head:
            active = True
        if active:
            table_rows.extend(rows)
            if any(re.match(r"всего", r[0], re.I) for r in rows if r):
                break
    if not table_rows or not re.search(r"паспорт.*программ|рекомендуемое количество часов", text, re.I | re.S):
        return None
    stages = []
    current = None
    for row in table_rows:
        cells = [unwrap_text(c) for c in row]
        label = cells[0] if cells else ""
        if re.match(r"Раздел\s+\d+", label, re.I):
            title = re.sub(r"^Раздел\s+\d+[. ]*", "", label, flags=re.I)
            hours = next((int(c) for c in reversed(cells[1:]) if c.isdigit()), None)
            current = {"title": title, "summary": "", "steps": [], "ordered": False}
            if hours is not None:
                current["hours"] = hours
            stages.append(current)
            continue
        if current is None and label and not label.isdigit() and not re.search(r"наименован|разделов", label, re.I):
            values = [c for c in cells[1:] if len(c) > 25 and not re.match(r"(?:ПК|ОК)\s*\d", c)]
            if values:
                h = next((int(c) for c in reversed(cells[2:]) if c.isdigit()), None)
                current = {"title": label, "summary": "", "steps": values, "ordered": False}
                if h is not None:
                    current["hours"] = h
                stages.append(current)
                continue
        if current is None or re.match(r"всего|промежуточная аттестация", label, re.I):
            continue
        # Ячейки с заданиями могут быть сдвинуты объединённой колонкой номера.
        content = [c for c in cells[1:] if len(c) > 25 and not re.match(r"Содержание(?: учебного материала)?$|(?:ПК|ОК)\s*\d", c, re.I)]
        for value in content:
            parts = re.split(r"(?:^|;\s*)[–—•]\s+", value)
            current["steps"].extend(x.strip(" ;") for x in parts if x.strip(" ;"))
    first = text.split("\f")[0]
    kind = re.search(r"(производственная|учебная)\s+практика", first, re.I)
    if not kind:
        kind = re.search(r"(производственн\w+|учебн\w+)\s+практик\w+", text, re.I)
    kind_value = ("Учебная практика" if kind and kind[1].lower().startswith("учеб") else "Производственная практика" if kind else "")
    module = re.search(r"(?m)^ПМ[. ]*\d+[. ]*[^\n]+", first)
    volume = re.search(r"[Оо]бщая\s+трудо[её]мкость[^.]{0,100}?составляет\s+(\d+)\s+час", text)
    total = int(volume[1]) if volume else None
    scope = text[max(0, volume.start()-200):volume.end()+100] if volume else ""
    sem = re.search(r"в\s+(\d+)\s+семестр", scope, re.I)
    week = re.search(r"\b(\d+)\s+недел", scope, re.I)
    control = "Зачёт с оценкой" if re.search(r"дифференцированный\s+зач[её]т", text, re.I) else ""
    result = {"practice_kind": kind_value, "practice_type": module[0] if module else "",
              "method": "", "form": "", "ze": None, "hours": {"total": total} if total else {},
              "semesters": [int(sem[1])] if sem else [], "weeks": int(week[1]) if week else None,
              "stages": stages, "control": [control] if control else [], "content": ""}
    warnings = []
    summed = sum(s.get("hours", 0) for s in stages)
    if total and summed and summed != total:
        warnings.append({"field": "hours", "reason": f"В паспорте {total} ч, сумма разделов {summed} ч; сохранены исходные значения"})
    result["_warnings"] = warnings
    return result
