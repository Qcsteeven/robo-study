"""Явно обозначенные подпункты в этапах практики, без разбиения дробей и версий."""
from __future__ import annotations

from copy import deepcopy
import re

from .util import normalize_list_markers


def stage_lists(items: list) -> list:
    out = deepcopy(items or [])
    for item in out:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        outline = list(re.finditer(r"(?<![\w/.])(\d{1,2})\.(\d{1,2})[.)]?\s+(?=[А-ЯЁ])", title))
        if (outline and len({m[1] for m in outline}) == 1
                and [int(m[2]) for m in outline] == list(range(1, len(outline)+1))):
            item["title"] = title[:outline[0].start()].rstrip(" .:")
            item["subtitles"] = [title[m.end():outline[i+1].start() if i+1 < len(outline) else len(title)].strip(" .;") for i,m in enumerate(outline)]
        if item.get("steps") or item.get("groups"):
            continue
        text = normalize_list_markers(item.get("summary") or "")
        item["summary"] = text
        headings = list(re.finditer(r"(?:^|(?<=[.!?;:])\s+)([А-ЯЁ][А-ЯЁа-яё -]{2,90}):\s*(?=•\s)", text))
        if headings and (len(headings) > 1 or headings[0].start() > 0):
            groups = []
            for i, heading in enumerate(headings):
                end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
                parts = re.split(r"(?:^|\s)•\s+", text[heading.end():end])
                steps = [p.strip() for p in parts if p.strip()]
                groups.append({"title": heading[1].strip(), "steps": steps, "ordered": False})
            item["summary"] = text[:headings[0].start()].strip()
            item["groups"] = groups
            continue
        matches = list(re.finditer(r"(?<![\w/.])(\d{1,2})[.)]\s+(?=\S)", text))
        ordered = True
        if not matches or [int(m[1]) for m in matches] != list(range(1, len(matches) + 1)):
            matches = list(re.finditer(r"(?:^|\s)•\s+", text))
            ordered = False
        if not matches:
            continue
        steps = [text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
                 for i, m in enumerate(matches)]
        if all(steps):
            item["summary"] = text[:matches[0].start()].strip()
            item["steps"], item["ordered"] = steps, ordered
    return out
