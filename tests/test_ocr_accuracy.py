"""Точность распознавания сканов УП и КУГ на демо-документах (ТЗ, раздел 9):
УП — не менее 95 % числовых значений и 98 % наименований дисциплин, КУГ — не менее 98 % обозначений."""
import pytest

from conftest import needs_tesseract
from demo_site import render
from demo_site.data import PROGRAMS
from robot_op.extract import curriculum, schedule
from robot_op.extract.ocr import OcrConfig
from robot_op.extract.util import norm

pytestmark = needs_tesseract


def expected_up(p):
    rows = {}
    for _, discs in p.groups:
        for d in discs:
            h = d.hours()
            aud = h["aud"] + (10 if p.up_mismatch == d.index else 0)
            nums = {"ze": h["ze"], "plan": h["plan"], "kont": h["kont"], "aud": aud, "sr": h["sr"], "control": h["control"]}
            for s, (lec, lab, pr) in d.sems.items():
                nums.update({f"{s}.lec": lec, f"{s}.lab": lab, f"{s}.pr": pr, f"{s}.sr": h["sr_sem"][s]})
            rows[d.index] = (d.name, nums, {"exam": d.exam, "zach": d.zach, "kp": d.kp, "kr": d.kr})
    return rows


@pytest.mark.parametrize("p", PROGRAMS, ids=[p.slug for p in PROGRAMS])
def test_up_scan_accuracy(p):
    got = curriculum.extract(render.up_scan_pdf(p), True, OcrConfig())
    by_index = {r["index"]: r for r in got["rows"]}
    exp = expected_up(p)
    names_ok = nums_total = nums_ok = 0
    for index, (name, nums, control) in exp.items():
        r = by_index.get(index)
        if r is None:
            nums_total += len(nums)
            continue
        names_ok += norm(r["name"]) == norm(name)
        flat = {**{k: r["hours"].get(k) or 0 for k in ("plan", "kont", "aud", "sr", "control")}, "ze": r["ze"]}
        for s, v in r["semesters"].items():
            flat.update({f"{s}.{k}": v.get(k, 0) for k in ("lec", "lab", "pr", "sr")})
        for k, v in nums.items():
            nums_total += 1
            nums_ok += flat.get(k, 0) == v
        for k, v in control.items():
            nums_total += 1
            nums_ok += r["control"].get(k, []) == sorted(v)
    assert names_ok / len(exp) >= 0.98, f"наименования: {names_ok}/{len(exp)}"
    assert nums_ok / nums_total >= 0.95, f"числа: {nums_ok}/{nums_total}"
    assert [s["weeks"] for s in got["semesters"]] == p.weeks
    # проверка сумм находит ровно заложенное расхождение
    failed = {r["index"] for r in got["rows"] if r["checks"]}
    assert failed == ({p.up_mismatch} if p.up_mismatch else set())


@pytest.mark.parametrize("p", [p for p in PROGRAMS if not p.no_kug], ids=lambda p: p.slug)
def test_kug_scan_accuracy(p):
    got = schedule.extract(render.kug_scan_pdf(p), True, OcrConfig())
    assert got["academic_year"] == p.kug["year"] and got["weeks"] == 52
    total = ok = 0
    for (course, group, codes), c in zip(p.kug["courses"], got["courses"]):
        assert c["course"] == course
        for w in range(1, 53):
            exp = codes.get(w) or ("К" if w >= 45 else "Т")
            total += 1
            ok += c["weeks"][w - 1] == exp
    assert ok / total >= 0.98, f"обозначения: {ok}/{total}"
    assert got["messages"] == []
