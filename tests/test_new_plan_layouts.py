from pathlib import Path
import pytest
from robot_op.extract import curriculum, schedule, pdf
from robot_op.extract.grid import Cell, Row, Grid
from robot_op.extract.ocr import OcrConfig

FIXTURES = Path(__file__).parent / "fixtures" / "education_plans"

def test_postgraduate_plan_preserves_numeric_indexes_and_control_hours():
    result = curriculum.extract((FIXTURES / "plan_350.pdf").read_bytes(), False, OcrConfig())
    rows = {r["index"]: r for r in result["rows"]}
    assert rows["2.1.1.1"]["name"] == "Структура и логика научного исследования"
    assert rows["2.1.1.1"]["semesters"]["1"] == {"lec": 12, "pr": 24, "sr": 72, "control": 0}
    assert rows["2.3.2"]["semesters"]["1"]["control"] == 36
    assert len(result["semesters"]) == 6
    assert all(s["weeks"] is None for s in result["semesters"])
    assert any("недель" in m for m in result["checks"]["messages"])

def test_spo_tiny_print_plan_preserves_all_semesters_and_uses_hours_not_credits():
    result = curriculum.extract((FIXTURES / "plan_304.pdf").read_bytes(), False, OcrConfig())
    rows = {r["index"]: r for r in result["rows"]}
    assert len(result["semesters"]) == 8
    assert result["semesters"][0]["weeks"] == 16.5
    assert result["semesters"][-1]["weeks"] is None
    russian = rows["ОУП.01"]
    assert russian["name"] == "Русский язык"
    assert russian["hours"]["plan"] == 96
    assert russian["semesters"]["1"]["lec"] == 30
    assert russian["semesters"]["2"]["pr"] == 4
    assert russian["control"] == {"exam": [2], "zach": [], "zach_oc": [1], "kp": [], "kr": []}
    assert not any("з.е." in m for m in result["checks"]["messages"])
    assert result["checks"]["failed"] == 0
    assert rows["МДК.02.02"]["name"] == "Программирование микроконтроллеров"

@pytest.mark.parametrize("text,expected", [("Семестр 1", (1,None)), ("Семестр 1 [16 3/6 нед]", (1,16.5)), ("Семестр 8 [ нед]", (8,None))])
def test_semester_headers_with_unknown_or_fractional_weeks(text, expected):
    assert curriculum._semester(text.lower()) == expected

@pytest.mark.parametrize("code", ["НИ","ПА","ИА","УП.01","ПП.02","ГИА","Пр"])
def test_calendar_preserves_multi_character_activity_codes(code):
    assert schedule._code(code) == code


def test_group_weekly_calendar_without_weekdays():
    def cell(col,text,end=None):
        return Cell(col*20,(end if end is not None else col)*20+19,0,19,col,col if end is None else end,text)
    rows = [Row(0,19,[cell(0,"Специальность"),cell(1,"Шифр"),cell(2,"группы"),cell(3,"Сентябрь",6)]),
            Row(20,39,[cell(3,"1"),cell(4,"2"),cell(5,"3"),cell(6,"4")]),
            Row(40,59,[cell(0,"Компьютерные системы"),cell(1,"09.02.01"),cell(2,"мКСК-26-1"),cell(3,""),cell(4,"УП.01"),cell(5,"ПП.01"),cell(6,"ГИА")])]
    result=schedule.parse_grid(Grid(rows,[(i*20,i*20+19) for i in range(7)]))
    assert result["courses"][0]["group"] == "мКСК-26-1"
    assert result["courses"][0]["weeks"] == ["Т","УП.01","ПП.01","ГИА"]
    assert result["courses"][0]["days"] == {}


def test_trimming_grid_edges_preserves_vertical_strokes_of_activity_letters():
    import numpy as np
    from robot_op.extract.ocr import _trim_lines
    ink = np.zeros((20, 36), dtype=bool)
    ink[:, 0:2] = True  # остаток рамки
    ink[1:19, 6:8] = True  # штрих Н, отделённый от рамки пробелом
    ink[1:19, 16:18] = True
    ink[9:11, 6:18] = True
    result = _trim_lines(ink, 12)
    assert result.sum() >= ink[:, 6:].sum()
