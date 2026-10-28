from robot_op.extract.curriculum import matrix, pairs_per_week, validate
from robot_op.extract.util import fix_index, semesters_from_code


def test_pairs_per_week_formula():
    # часы аудиторных занятий за семестр ÷ 2 ÷ число учебных недель
    assert pairs_per_week(32, 16) == 1.0
    assert pairs_per_week(16, 16) == 0.5
    assert pairs_per_week(48, 16) == 1.5
    assert pairs_per_week(0, 16) is None


def test_control_codes():
    assert semesters_from_code("58") == [5, 8]
    assert semesters_from_code("1234") == [1, 2, 3, 4]
    assert semesters_from_code("9A") == [9, 10]


def test_index_after_ocr():
    assert fix_index("61.0.01") == "Б1.О.01"
    assert fix_index("1.8.02") == "Б1.В.02"
    assert fix_index("ББ1.00.05") == "Б1.О.05"


def row(**kw):
    base = {"index": "Б1.О.01", "name": "Физика", "block": "Блок 1. Дисциплины (модули)", "part": "Обязательная часть",
            "control": {"exam": [3], "zach": [2]}, "ze": 7,
            "hours": {"plan": 252, "kont": 128, "aud": 128, "sr": 88, "control": 36},
            "semesters": {"2": {"lec": 32, "lab": 16, "pr": 16, "sr": 38}, "3": {"lec": 32, "lab": 16, "pr": 16, "sr": 50}}}
    base.update(kw)
    return base


def test_validation_detects_sum_mismatch():
    ok, bad = row(), row(index="Б1.О.02", hours={"plan": 252, "kont": 128, "aud": 138, "sr": 88, "control": 36})
    messages = validate([ok, bad])
    assert ok["checks"] == [] and len(messages) == 1
    assert "«Ауд.» 138 ≠ сумме по семестрам 128" in bad["checks"][0]


def test_matrix_groups_and_pairs():
    up = {"semesters": [{"number": n, "weeks": 16} for n in range(1, 5)], "rows": [row()]}
    validate(up["rows"])
    m = matrix(up)
    assert m["groups"][0]["title"] == "Блок 1. Обязательная часть"
    cell = m["groups"][0]["rows"][0]["cells"]["2"]
    assert (cell["lec"], cell["lab"], cell["pr"], cell["control"]) == (1.0, 0.5, 0.5, "Зач")
    assert m["groups"][0]["rows"][0]["cells"]["3"]["control"] == "Экз"
