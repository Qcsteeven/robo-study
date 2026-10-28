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


def row(index, name, sems, ze=3, control=None, module=False):
    return {"index": index, "name": name, "ze": ze, "is_module": module,
            "semesters": {str(s): {"lec": 16, "sr": 20} for s in sems},
            "control": control or {"zach": sems[-1:]}, "hours": {}, "checks": []}


def test_plan_view_courses_and_shelves():
    up = {"semesters": [{"number": n, "weeks": 16} for n in (1, 2, 3, 4)], "rows": [
        row("Б1.О.01", "Математика", [1, 2], ze=8, control={"exam": [2], "zach": [1]}),
        row("Б1.О.02", "Программирование", [1], ze=4),
        row("Б1.В.01", "Модуль профильной подготовки", [3, 4], ze=6, module=True),
        row("Б1.В.01.01", "Машинное обучение", [3], ze=3, control={"exam": [3]}),
        row("Б1.В.ДВ.01", "Дисциплины по выбору", [4], ze=3),
        row("Б1.В.ДВ.01.01", "Нейронные сети", [4], ze=3),
        row("Б1.В.ДВ.01.02", "Компьютерное зрение", [4], ze=3),
        row("ФТД.01", "Образовательный форсайт", [2], ze=2),
    ]}
    v = build(up, links={"математика": "/op/x/doc/1"}, key=str.lower)
    assert [c["number"] for c in v["courses"]] == [1, 2]
    first = v["courses"][0]["semesters"][0]
    assert [i["name"] for i in first["items"]] == ["Программирование", "Математика"]     # по порядку категорий
    assert first["items"][1]["url"] == "/op/x/doc/1" and first["items"][1]["control"] == "Зач"
    second = v["courses"][0]["semesters"][1]
    assert any(i["optional"] for i in second["items"])                                  # факультатив отмечен
    names = {i["name"] for s in v["courses"][1]["semesters"] for i in s["items"]}
    assert "Модуль профильной подготовки" not in names                                   # модуль не дисциплина
    assert "На выбор: Нейронные сети или компьютерное зрение" in names                   # блок по выбору — одна позиция
    shelves = {s["key"]: s for s in v["shelves"]}
    assert shelves["math"]["ze"] == 8 and v["total_ze"] == 18                             # факультатив в объём не входит
    assert sum(s["share"] for s in v["shelves"]) in (99, 100, 101)


def test_plural():
    assert [plural(n, "а", "ы", "") for n in (1, 3, 5, 11, 21, 24)] == ["а", "ы", "", "", "а", "ы"]
