"""Контекст веб-страницы ОП по итоговым JSON-файлам (ТЗ, шаг 12). Формат — как sample/op_09.03.01_2023.json дизайна."""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select

from ..catalog import DOC_ORDER, DOC_TYPE_TITLES, DOC_TYPES, LEVEL_TONE, REVIEW_STATUSES
from ..extract import curriculum
from ..extract.schedule import LEGEND
from ..models import Document, JsonVersion, Program

QUALIFICATION = {"bachelor": "Бакалавр", "specialist": "Специалист", "master": "Магистр",
                 "spo": "Специалист среднего звена", "postgraduate": "Исследователь. Преподаватель-исследователь"}


def latest_final(ses, doc_id: int) -> dict | None:
    v = ses.scalar(select(JsonVersion).where(JsonVersion.document_id == doc_id, JsonVersion.kind == "final")
                   .order_by(JsonVersion.version.desc()))
    return v.data if v else None


def _key(name: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]", "", (name or "").lower().replace("ё", "е"))


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


def build(ses, prog: Program) -> dict:
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
    oop_data = (oop or {}).get("data") or {}
    matrix = curriculum.matrix(up_data) if up_data.get("rows") else None
    up_rows = {_key(r["name"]): r for r in up_data.get("rows", [])}

    op = {
        "id": prog.id, "code": prog.code, "direction": prog.direction, "profile": prog.profile or prog.block_title,
        "level": prog.level_title, "level_key": prog.level, "level_tone": LEVEL_TONE.get(prog.level, "blue"),
        "form": prog.form, "year": prog.year,
        "duration": oop_data.get("duration") or _years(len(up_data.get("semesters", []))),
        "qualification": (oop_data.get("qualification") or QUALIFICATION.get(prog.level, "")).capitalize(),
        "source_url": prog.source_url,
        "summary": (oop or {}).get("summary"),
        "characteristic": oop_data.get("characteristic", ""),
        "areas": oop_data.get("areas", []),
        "task_types": oop_data.get("task_types", []),
    }

    schedule = None
    kd = (kug or {}).get("data") or {}
    if kd.get("courses"):
        used = {c for course in kd["courses"] for c in course["weeks"]}
        schedule = {
            "academic_year": kd.get("academic_year", ""),
            "months": kd.get("months") or [],
            "weeks": kd.get("weeks", 52),
            "legend": [{"code": k, "label": v} for k, v in LEGEND.items() if k in used or k == "Т"],
            "courses": [{"course": c["course"], "group": c.get("group", ""), "weeks": c["weeks"]} for c in kd["courses"]],
        }

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
                            "summary": f.get("summary") or "", "goal": data.get("goal", ""), "status": d.status,
                            "url": f"/op/{prog.id}/doc/{d.id}", "pdf_url": d.url})

    practices = []
    for d in by_type.get("practice", []):
        f = finals.get(d.id) or {}
        data = f.get("data") or {}
        sems = data.get("semesters") or []
        if not sems:
            row = up_rows.get(_key(d.title))
            sems = sorted({s for v in (row or {}).get("control", {}).values() for s in v})
        practices.append({"id": d.id, "name": d.title, "semester": _sem_text(sems),
                          "weeks": data.get("weeks") or "—", "summary": f.get("summary") or "", "status": d.status,
                          "url": f"/op/{prog.id}/doc/{d.id}", "pdf_url": d.url, "_order": sems[0] if sems else 99})
    practices.sort(key=lambda p: (p.pop("_order"), p["name"]))

    gd, fd = (gia or {}).get("data") or {}, (fos or {}).get("data") or {}
    gia_items = []
    if gd.get("forms"):
        gia_items.append({"label": "Формы ГИА", "value": "; ".join(f.rstrip(".") for f in gd["forms"])})
    if gd.get("ze"):
        gia_items.append({"label": "Объём", "value": f"{gd['ze']} з. е."})
    if gd.get("weeks"):
        gia_items.append({"label": "Продолжительность", "value": f"{gd['weeks']} нед."})
    if fd.get("competencies"):
        gia_items.append({"label": "Проверяемые компетенции", "value": ", ".join(fd["competencies"])})
    gia_ctx = {
        "items": gia_items,
        "summary": " ".join(s for s in ((gia or {}).get("summary"), (fos or {}).get("summary")) if s),
        "vkr_topics": fd.get("tasks", [])[:10],
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
            statuses = Counter(d.status for d in group)
            worst = next((s for s in ("error", "needs_review", "partial", "unknown_template") if statuses.get(s)), None)
            status = "needs_review" if worst in ("partial", "unknown_template", "needs_review") else (worst or "processed")
            parts = [f"{statuses.get('processed', 0)} обработано"]
            labels = {"partial": "частично", "needs_review": "требуют проверки", "unknown_template": "неизвестный шаблон",
                      "error": "ошибка", "skipped": "пропущено", "queued": "в очереди"}
            parts += [f"{n} — {labels.get(s, s)}" for s, n in statuses.items() if s != "processed"]
            title = ("Рабочие программы дисциплин" if t == "rpd" else "Рабочие программы практик") + f" — {len(group)} док."
            documents.append({"type": DOC_TYPES[t], "title": title, "status": status, "note": ", ".join(parts),
                              "url": "#discipliny" if t == "rpd" else "#praktiki",
                              "json_url": f"/api/programs/{prog.id}/documents?type={t}", "group": True})
        for d in group if not (t in ("rpd", "practice") and len(group) > 1) else []:
            documents.append({"type": DOC_TYPES[t], "title": DOC_TYPE_TITLES[t] if t not in ("rpd", "practice") else d.title,
                              "status": d.status, "note": d.note, "url": d.url,
                              "json_url": f"/api/documents/{d.id}/json", "doc_id": d.id})
    review = [{"type": DOC_TYPES[d.doc_type], "title": d.title, "note": d.note, "status": d.status,
               "url": f"/op/{prog.id}/doc/{d.id}"} for d in docs if d.status in REVIEW_STATUSES]
    errors = [{"type": DOC_TYPES[d.doc_type], "title": d.title, "note": d.note} for d in docs if d.status == "error"]
    return {"op": op, "curriculum": matrix, "schedule": schedule, "disciplines": disciplines, "practices": practices,
            "gia": gia_ctx, "documents": documents, "review": review, "errors": errors,
            "up_checks": up_data.get("checks", {}).get("messages", [])}
