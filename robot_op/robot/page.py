"""Контекст веб-страницы ОП по итоговым JSON-файлам (ТЗ, шаг 12). Формат — как sample/op_09.03.01_2023.json дизайна."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlencode

from sqlalchemy import select

from ..catalog import DOC_ORDER, DOC_TYPE_TITLES, DOC_TYPES, LEVEL_TONE
from ..extract import curriculum
from ..extract.quality import validate as validate_fields
from ..extract.util import discipline_name
from ..extract.schedule import LEGEND
from ..extract.tables import vkr_topics
from ..models import Document, JsonVersion, Program
from . import plan_view, program_summary
from .llm import gia_characteristic
from .teachers import teacher_discipline_key

QUALIFICATION = {"bachelor": "Бакалавр", "specialist": "Специалист", "master": "Магистр",
                 "spo": "Специалист среднего звена", "postgraduate": "Исследователь. Преподаватель-исследователь"}


def latest_final(ses, doc_id: int) -> dict | None:
    v = ses.scalar(select(JsonVersion).where(JsonVersion.document_id == doc_id, JsonVersion.kind == "final")
                   .order_by(JsonVersion.version.desc()))
    return v.data if v else None


def _key(name: str) -> str:
    name = discipline_name(name or "").lower().replace("ё", "е")
    return re.sub(r"[^а-яёa-z0-9]", "", name)


def _matches_curriculum(row: dict, data: dict) -> bool | None:
    hours = data.get("hours") or {}
    planned = row.get("hours") or {}
    facts = ((plan_view.semester_numbers(row), set(data.get("semesters") or [])),
             (row.get("ze"), data.get("ze")),
             (planned.get("plan"), hours.get("total")),
             (planned.get("aud"), hours.get("aud")))
    matches = [expected == actual for expected, actual in facts if expected and actual]
    return all(matches) if matches else None


def discipline_links(program_id: str, rows: list[dict], docs: list[Document], finals: dict) -> dict[str, str]:
    """Связать одноимённые дисциплины с РПД по семестрам и часам, если есть несколько вариантов."""
    candidates = {}
    for doc in docs:
        if doc.doc_type not in ("rpd", "practice"):
            continue
        data = ((finals.get(doc.id) or {}).get("data") or {})
        for name in {_key(doc.title), _key(data.get("discipline"))} - {""}:
            candidates.setdefault(name, []).append((f"/op/{program_id}/doc/{doc.id}", data))
    positions = [row for row in rows if row.get("index") and
                 (not row.get("is_module") or plan_view.is_choice(row["index"]))]
    counts = Counter(_key(row["name"]) for row in positions)
    links = {name: entries[0][0] for name, entries in candidates.items() if len(entries) == 1}
    for row in positions:
        index, name = row["index"], _key(row["name"])
        entries = candidates.get(name, [])
        if len(entries) > 1:
            entries = [(url, data) for url, data in entries if _matches_curriculum(row, data)]
        elif entries and counts[name] > 1 and _matches_curriculum(row, entries[0][1]) is False:
            entries = []
        url = entries[0][0] if len(entries) == 1 else f"/op/{program_id}/discipline?" + urlencode({"index": index})
        links[index] = url
        links.setdefault(name, url)
    return links


def _years(n_semesters: int) -> str:
    if not n_semesters:
        return ""
    y = n_semesters / 2
    if y.is_integer():
        y = int(y)
        word = "год" if y == 1 else ("года" if 2 <= y <= 4 else "лет")
        return f"{y} {word}"
    return f"{str(y).replace('.', ',')} года"


def _sem_text(sems) -> str:
    return ", ".join(str(s) for s in sorted(set(int(x) for x in sems))) if sems else "—"


MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
               "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def calendar_grid(kd: dict, fallback_year: int) -> dict:
    """График учебного процесса как в документе: по курсу шесть строк пн…сб, колонка — неделя с понедельника,
    в каждой ячейке — обозначение дня. Даты считаются от 1 сентября учебного года; дни до 1 сентября и после
    31 августа заштрихованы («обучение не ведётся»). Колонка относится к месяцу, на который приходится больше
    её дней пн…сб внутри учебного года, при равенстве — к более раннему (28.09–03.10 — сентябрь)."""
    m = re.match(r"(20\d\d)", kd.get("academic_year") or "")
    y0 = int(m.group(1)) if m else fallback_year
    first, last = date(y0, 9, 1), date(y0 + 1, 8, 31)
    weekly = kd.get("mode") == "weekly"
    start = first if weekly else first - timedelta(days=first.weekday())
    n_cols = kd["weeks"] if weekly else (last - start).days // 7 + 1
    mondays = [start + timedelta(weeks=i) for i in range(n_cols)]
    n_weeks = kd.get("weeks") or 52

    months: list[list] = []
    for mon in mondays:
        inside = [d for d in (mon + timedelta(days=k) for k in range(6)) if first <= d <= last]
        month = mon.month if weekly else max(dict.fromkeys(d.month for d in inside), key=[d.month for d in inside].count)
        name = MONTH_NAMES[month - 1]
        if months and months[-1][0] == name:
            months[-1][1] += 1
        else:
            months.append([name, 1])
    header = [{"day": d, "cells": [{"n": (mon + timedelta(days=k)).day,
                                    "out": not first <= mon + timedelta(days=k) <= last} for mon in mondays]}
              for k, d in enumerate(WEEKDAYS)] if not weekly else []

    used = set()
    courses = []
    for c in kd["courses"]:
        days = c.get("days") or {}
        rows = []
        for k, d in enumerate(["нед."] if kd.get("mode") == "weekly" else WEEKDAYS[:6]):
            cells = []
            for i, mon in enumerate(mondays):
                if not first <= mon + timedelta(days=k) <= last:
                    cells.append("out")
                    continue
                codes = days.get(d)
                # старые JSON-файлы — только по неделям: обозначение недели на каждый её день
                code = codes[i] if codes and i < len(codes) else (c["weeks"][i] if i < len(c["weeks"]) else "")
                code = "out" if kd.get("mode") == "weekly" and code == "*" else "" if code == "Т" else code
                used.add(code)
                cells.append(code)
            rows.append({"day": d, "cells": cells})
        courses.append({"course": c["course"], "group": c.get("group", ""), "rows": rows})
    legend = [{"code": "", "label": LEGEND["Т"]}] + [{"code": k, "label": v} for k, v in LEGEND.items() if k in used and k != "Т"]
    for code in sorted(used):
        if re.fullmatch(r"(?:УП|ПП)(?:\.\d+)?", code):
            legend.append({"code": code, "label": "учебная практика" if code.startswith("УП") else "производственная практика"})
    if "*" in used:
        legend.append({"code": "*", "label": "нерабочие праздничные дни"})
    legend.append({"code": "out", "label": "обучение не ведётся"})
    return {"academic_year": kd.get("academic_year") or f"{y0}/{y0 + 1}", "months": months, "header": header,
            "week_numbers": [i + 1 if i < n_weeks else None for i in range(n_cols)], "courses": courses, "legend": legend, "needs_review": bool(kd.get("messages")), "mode": kd.get("mode", "daily")}


def saved_context(ses, prog: Program) -> dict:
    """Дополнить ранее сохранённую страницу траекториями из уже разобранного УП."""
    context = prog.page
    plan = context.get("plan")
    if not plan or "tracks" in plan:
        return context
    ids = ses.scalars(select(Document.id).where(Document.program_id == prog.id, Document.doc_type == "up")
                     .order_by(Document.id))
    for doc_id in ids:
        data = (latest_final(ses, doc_id) or {}).get("data")
        if data:
            if plan_view.track_rows(data.get("rows") or []):
                return dict(context, plan=build(ses, prog)["plan"])
            break
    return context


def build(ses, prog: Program, *, teacher_directory: dict | None = None) -> dict:
    docs = ses.scalars(select(Document).where(Document.program_id == prog.id)).all()
    finals = {d.id: latest_final(ses, d.id) for d in docs}
    by_type: dict[str, list[Document]] = {}
    for d in docs:
        by_type.setdefault(d.doc_type, []).append(d)

    def first_final(t: str) -> tuple[Document | None, dict | None]:
        for d in by_type.get(t, []):
            f = finals.get(d.id)
            if f and f.get("data"):
                return d, f
        return (by_type.get(t, [None])[0], None)

    up_doc, up = first_final("up")
    kug_doc, kug = first_final("kug")
    oop_doc, oop = first_final("oop")
    gia_doc, gia = first_final("gia")
    fos_doc, fos = first_final("fos_gia")

    up_data = (up or {}).get("data") or {}
    oop_data = validate_fields("oop", (oop or {}).get("data") or {})
    up_rows = {_key(r["name"]): r for r in up_data.get("rows", [])}

    op = {
        "id": prog.id, "code": prog.code, "direction": prog.direction, "profile": prog.profile or prog.block_title,
        "level": prog.level_title, "level_key": prog.level, "level_tone": LEVEL_TONE.get(prog.level, "blue"),
        "form": prog.form, "year": prog.year,
        "duration": oop_data.get("duration") or _years(len(up_data.get("semesters", []))),
        "qualification": (oop_data.get("qualification") or QUALIFICATION.get(prog.level, "")).capitalize(),
        "source_url": prog.source_url,
        "summary": program_summary.clean_summary((oop or {}).get("summary")),
        "characteristic": program_summary.fallback(oop_data, {"profile": prog.profile or prog.block_title}),
        "areas": program_summary.professional_areas(oop_data.get("areas", [])),
        "task_types": oop_data.get("task_types", []),
    }

    kd = (kug or {}).get("data") or {}
    schedule = calendar_grid(kd, prog.year) if kd.get("courses") else None

    disciplines = []
    for d in sorted(by_type.get("rpd", []), key=lambda x: x.title.lower()):
        f = finals.get(d.id) or {}
        data = f.get("data") or {}
        row = up_rows.get(_key(d.title)) or up_rows.get(_key(data.get("discipline", "")))
        if row:
            sems = sorted(set(int(s) for s in row["semesters"]) | {s for v in row["control"].values() for s in v})
            control = ", ".join(sorted({curriculum.CONTROL_LABELS[k] for k, v in row["control"].items() if v},
                                       key=list(curriculum.CONTROL_LABELS.values()).index))
        else:
            sems = data.get("semesters", [])
            control = ", ".join(c.capitalize() for c in data.get("control", []))
        disciplines.append({"id": d.id, "name": d.title, "semesters": _sem_text(sems), "control": control or "—",
                            "summary": f.get("summary") or "", "goal": data.get("goal", ""),
                            "url": f"/op/{prog.id}/doc/{d.id}", "pdf_url": d.url})

    practices = []
    for d in by_type.get("practice", []):
        f = finals.get(d.id) or {}
        data = f.get("data") or {}
        sems = data.get("semesters") or []
        if not sems:
            row = up_rows.get(_key(d.title))
            sems = sorted({s for v in (row or {}).get("control", {}).values() for s in v})
        practices.append({"id": d.id, "name": d.title + (" · " + data["practice_type"] if re.match(r"ПМ[. ]", data.get("practice_type", "")) else ""), "semester": _sem_text(sems),
                          "weeks": data.get("weeks") or "—", "summary": f.get("summary") or "",
                          "url": f"/op/{prog.id}/doc/{d.id}", "pdf_url": d.url, "_order": sems[0] if sems else 99})
    practices.sort(key=lambda p: (p.pop("_order"), p["name"]))

    gd, fd = (gia or {}).get("data") or {}, (fos or {}).get("data") or {}
    gia_items = []
    if gd.get("forms"):
        gia_items.append({"label": "Формы ГИА", "value": "; ".join(f.rstrip(".") for f in gd["forms"])})
    if gd.get("ze"):
        gia_items.append({"label": "Объём", "value": f"{gd['ze']} з. е."})
    elif gd.get("hours"):
        gia_items.append({"label": "Объём", "value": f"{gd['hours']} ч"})
    if gd.get("weeks"):
        gia_items.append({"label": "Продолжительность", "value": f"{gd['weeks']} нед."})
    if fd.get("competencies"):
        gia_items.append({"label": "Проверяемые компетенции", "value": ", ".join(fd["competencies"])})
    gia_ctx = {
        "items": gia_items,
        # описание ГИА и ВКР — не больше двух абзацев: из программы ГИА, а если её описания нет — из ФОС ГИА
        "summary": gia_characteristic((gia or {}).get("summary")) or gia_characteristic((fos or {}).get("summary")),
        # темы ВКР — только из таблицы тем программы ГИА; в тексте ФОС ГИА это обрывки абзацев, а не темы
        "vkr_topics": [t for t in vkr_topics([[t] for t in gd.get("vkr_topics") or []]) if len(t) < 200][:10],
        "criteria": gd.get("criteria") or fd.get("criteria") or "",
        "gia_url": f"/op/{prog.id}/doc/{gia_doc.id}" if gia_doc else None,
        "fos_url": f"/op/{prog.id}/doc/{fos_doc.id}" if fos_doc else None,
    }

    documents = []
    for t in DOC_ORDER:
        group = by_type.get(t, [])
        if not group:
            continue
        if t in ("rpd", "practice") and len(group) > 1:
            title = ("Рабочие программы дисциплин" if t == "rpd" else "Рабочие программы практик") + f" — {len(group)}"
            documents.append({"type": DOC_TYPES[t], "title": title, "url": "#up" if t == "rpd" else "#praktiki",
                              "group": True})
        for d in group if not (t in ("rpd", "practice") and len(group) > 1) else []:
            documents.append({"type": DOC_TYPES[t], "title": DOC_TYPE_TITLES[t] if t not in ("rpd", "practice") else d.title,
                              "url": d.url, "doc_id": d.id})
    # карусель по семестрам и полки категорий; дисциплины ведут на страницы своих РПД и программ практик
    links = discipline_links(prog.id, up_data.get("rows", []), docs, finals)
    plan = plan_view.build(up_data, links, key=_key)

    if teacher_directory is None:
        teacher_directory = (prog.page or {}).get("teacher_directory", {})
    if plan and teacher_directory:
        for track in plan["tracks"] or [plan]:
            for semester in track["semesters"]:
                for item in semester["items"]:
                    names = [option["name"] for option in item["options"]] or [item["name"]]
                    candidates = {}
                    for name in names:
                        for teacher in teacher_directory.get(teacher_discipline_key(name), []):
                            candidates.setdefault((teacher["name"], teacher.get("url")), teacher)
                    if candidates:
                        item["teachers"] = list(candidates.values())

    return {"op": op, "plan": plan, "schedule": schedule, "disciplines": disciplines, "practices": practices,
            "teacher_directory": teacher_directory,
            "gia": gia_ctx, "documents": documents}
