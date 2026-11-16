import numpy as np

from robot_op.extract.grid import detect_grid
from robot_op.extract.curriculum import _renumber, _semester, matrix, pairs_per_week, validate
from robot_op.extract.util import discipline_name, fix_index, semesters_from_code


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
    # искажения из реального скана УП ИРНИТУ
    assert fix_index("Б1.5.01.02") == "Б1.Б.01.02"
    assert fix_index("Б1.65.05") == "Б1.Б.05"
    assert fix_index("Б1.8В.02.09") == "Б1.В.02.09"
    assert fix_index("БТ.В.03.02") == "Б1.В.03.02"
    assert fix_index("ФТД.О1") == "ФТД.01"
    assert fix_index("Б1.О.01") == "Б1.О.01"


def test_semester_label_after_ocr():
    assert _semester("семестр 3 [16 нед]") == (3, 16)
    assert _semester("cemectn 1 [16 нёл]") == (1, 16)
    assert _semester("семесто 6116 нел]") == (6, 16)       # скобка «[» прочитана как «1»
    assert _semester("курс 1") is None
    # номер семестра, прочитанный с ошибкой, восстанавливается по порядку колонок
    assert [s[0] for s in _renumber([(1, 16, 0, 4), (7, 16, 5, 9), (3, 16, 10, 14)])] == [1, 2, 3]


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


def test_validation_eios_and_free_electives():
    # «Ауд.» = Лек + Лаб + Пр; ЭИОС входит в «Конт. раб.» (реальный УП ИРНИТУ, «История России»)
    hist = row(index="Б1.Б.01.07", control={"exam": [7]}, ze=4,
               hours={"plan": 144, "kont": 116, "aud": 80, "sr": 28, "control": 0},
               semesters={"7": {"lec": 48, "lab": 0, "pr": 32, "eios": 36, "sr": 28}})
    # элективные курсы по физкультуре: 328 ч без з.е.; модуль с ними — 2 з.е. на 400 ч
    module = row(index="Б1.Б.05", ze=2, hours={"plan": 400, "kont": 208, "aud": 208, "sr": 192, "control": 0},
                 semesters={"1": {"pr": 208, "sr": 192}})
    pe = row(index="Б1.Б.05.02", ze=0, hours={"plan": 328, "kont": 176, "aud": 176, "sr": 152, "control": 0},
             semesters={"2": {"pr": 176, "sr": 152}})
    bad_kont = row(index="Б1.Б.01.08", ze=4, hours={"plan": 144, "kont": 100, "aud": 80, "sr": 44, "control": 0},
                   semesters={"7": {"lec": 48, "pr": 32, "eios": 36, "sr": 44}})
    validate([hist, module, pe, bad_kont])
    assert hist["checks"] == [] and module["checks"] == [] and pe["checks"] == []
    assert any("Конт. раб." in c for c in bad_kont["checks"])


def test_scanned_grid_keeps_last_course_above_faint_dotted_footer_line():
    raster = np.full((140, 400), 255, dtype=np.uint8)
    raster[[15, 45, 115], 20:380] = 0
    raster[15:116, [20, 160, 379]] = 0
    for x in range(20, 378, 6):
        raster[80, x:x + 4] = 195

    grid = detect_grid(raster, recover_faint_rules=True)

    assert [(row.y0, row.y1) for row in grid.rows] == [(16, 44), (46, 79), (81, 114)]
    assert grid.rows[1].cell_at(1).y1 == 79


def test_discipline_name_fixes_confirmed_ocr_of_preposition():
    assert discipline_name("Подготовка к сдаче квалификационного экзамена NO иностранному языку") == (
        "Подготовка к сдаче квалификационного экзамена по иностранному языку"
    )


def test_discipline_name_preserves_confirmed_latin_iiot_acronym():
    titles = [
        "Анализ и оптимизация сетей и протоколов связи в системах IIoT",
        "Исследование и разработка методов защиты и обеспечения кибербезопасности в IIoT",
        "Проектирование и оптимизация архитектуры систем IIoT",
        "Промышленные протоколы и стандарты обмена данных в системах IIoT",
        "Мониторинг систем IIoT",
        "IIoT в интеллектуальной робототехнике",
        "Построение цифровых двойников на основе IIoT",
        "Социальные аспекты применениях технологии IIoT",
    ]
    for title in titles:
        assert discipline_name(title.replace("IIoT", "Пот")) == title
    assert discipline_name("Пот и секреты кожных желез") == "Пот и секреты кожных желез"


def test_split_semester_header_recovers_practical_and_independent_hours():
    from robot_op.extract.curriculum import find_layout, parse_grid
    from robot_op.extract.grid import Cell, Grid, Row

    widths = [35, 130, 40, 40, 40, 40, 40, 40, 40, 11, 30, 8, 32, 35]
    starts = [sum(widths[:i]) for i in range(len(widths))]
    columns = [(x, x + w) for x, w in zip(starts, widths)]

    def line(*cells):
        return Row(0, 20, [Cell(columns[a][0], columns[b][1], 0, 20, a, b, t) for a, b, t in cells])

    grid = Grid([
        line((6, 12, "Курс 2"), (13, 13, "Закрепленная")),
        line((2, 5, "Семестр 3 [14 нед]"), (6, 8, "Семестр 4"), (9, 11, "8 нед.")),
        line((0, 0, "Индекс"), (1, 1, "Наименование"),
             (2, 2, "Лек"), (3, 3, "Лаб"), (4, 4, "Пр"), (5, 5, "СР"),
             (6, 6, "Лек"), (7, 7, "Лаб"), (8, 9, "Пр"), (10, 12, "СР"), (13, 13, "Код")),
        line((0, 0, "Б1.В.01"), (1, 1, "Промышленные сети"),
             (6, 6, "8"), (8, 8, "16"), (12, 12, "48"), (13, 13, "121")),
    ], columns)
    layout = find_layout(grid)
    sem = layout.semesters[4]
    assert sem == {"weeks": 8, "cols": {"lec": 6, "lab": 7, "pr": 8, "sr": 12}}
    row = parse_grid(grid, layout)[0]
    assert row["semesters"]["4"] == {"lec": 8, "lab": 0, "pr": 16, "sr": 48}
