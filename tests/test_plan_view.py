from robot_op.robot.plan_view import build, categorize
from robot_op.web.app import plural


def test_categories_by_name():
    # названия из реальных учебных планов ИРНИТУ
    assert categorize("Программирование на Python") == "it"
    assert categorize("Базы данных") == "it"
    assert categorize("Машинное обучение") == "ai"
    assert categorize("Глубокое обучение в технологиях обработки естественного языка") == "ai"
    assert categorize("Введение в большие языковые модели") == "ai"
    assert categorize("Математика") == "math"
    assert categorize("Сопротивление материалов") == "math"
    assert categorize("Иностранный язык") == "hum"
    assert categorize("Защита интеллектуальной собственности") == "hum"
    assert categorize("Производственная практика: преддипломная практика") == "prac"
    assert categorize("Спортивные игры") == "sport"
    # фрагмент внутри чужого слова совпадением не считается
    assert categorize("Насосные и воздуходувные станции") == "prof"          # не «танц»
    assert categorize("Основы промышленного водоснабжения") == "prof"         # не «мышлен»
    assert categorize("Управление ИТ-проектами") == "hum"                     # не «прав»
    assert categorize("Водопроводные сети") == "prof"


def row(index, name, sems, ze=3, control=None, module=False, hours=None):
    return {"index": index, "name": name, "ze": ze, "is_module": module,
            "semesters": {str(s): dict(hours or {"lec": 16, "sr": 20}) for s in sems},
            "control": control or {"zach": sems[-1:]}, "hours": {}, "checks": []}


def test_plan_view_semesters_and_shelves():
    up = {"semesters": [{"number": n, "weeks": 16} for n in (1, 2, 3, 4)], "rows": [
        row("Б1.О.01", "Математика", [1, 2], ze=8, control={"exam": [2], "zach": [1]}),
        row("Б1.О.02", "Программирование", [1], ze=4, hours={"lec": 16, "pr": 8, "lab": 33, "sr": 20}),
        row("Б1.В.01", "Модуль профильной подготовки", [3, 4], ze=6, module=True),
        row("Б1.В.01.01", "Машинное обучение", [3], ze=3, control={"exam": [3]}),
        row("Б1.В.ДВ.01", "Дисциплины по выбору", [4], ze=3),
        row("Б1.В.ДВ.01.01", "Нейронные сети", [4], ze=3),
        row("Б1.В.ДВ.01.02", "Компьютерное зрение", [4], ze=3),
        row("ФТД.01", "Образовательный форсайт", [2], ze=2),
        row("Б2.О.01(У)", "Учебная практика", [2], ze=3, hours={"sr": 108}),
    ]}
    v = build(up, links={"математика": "/op/x/doc/1"}, key=str.lower)
    assert [(s["number"], s["course"]) for s in v["semesters"]] == [(1, 1), (2, 1), (3, 2), (4, 2)]
    first = v["semesters"][0]
    assert [i["name"] for i in first["items"]] == ["Программирование", "Математика"]     # по порядку категорий
    assert first["items"][1]["url"] == "/op/x/doc/1" and first["items"][1]["control"] == "Зач"
    # пары за семестр = часы ÷ 2: лекции 8 + 8, практики 4, лабораторные 16,5
    assert (first["items"][0]["lec"], first["items"][0]["pr"], first["items"][0]["lab"], first["items"][0]["pairs"]) == (8, 4, 16.5, 28.5)
    assert (first["lec"], first["pr"], first["lab"], first["pairs"], first["per_week"]) == (16, 4, 16.5, 36.5, 2.3)
    second = v["semesters"][1]
    assert any(i["optional"] for i in second["items"])                                  # факультатив отмечен
    assert second["pairs"] == 8                                                          # но в итог не входит
    assert second["items"][-1]["practice"] and second["items"][-1]["name"] == "Учебная практика"
    names = {i["name"] for s in v["semesters"][2:] for i in s["items"]}
    assert "Модуль профильной подготовки" not in names                                   # модуль не дисциплина
    assert "На выбор: Нейронные сети или компьютерное зрение" in names                   # блок по выбору — одна позиция
    shelves = {s["key"]: s for s in v["shelves"]}
    assert shelves["math"]["ze"] == 8 and v["total_ze"] == 21                             # факультатив в объём не входит
    assert sum(s["share"] for s in v["shelves"]) in (99, 100, 101)


def test_plural():
    assert [plural(n, "а", "ы", "") for n in (1, 3, 5, 11, 21, 24)] == ["а", "ы", "", "", "а", "ы"]
