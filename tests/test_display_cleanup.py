import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from robot_op.extract import sections
from robot_op.robot import doc_view, plan_view
from robot_op.robot.page import build
from robot_op.models import Document, JsonVersion, Program
from robot_op.web.app import create_app
from conftest import demo_settings

PRACTICE = """1 Вид практики, тип, способ и формы её проведения
Вид практики – Производственная практика
Тип практики – Производственная практика: технологическая (проектно-
технологическая) практика
Способ проведения –
Форма проведения –
2 Перечень планируемых результатов обучения
Результаты
3 Место практики в структуре ООП, её объём и продолжительность
6 семестр, 6 ЗЕТ, 4 недели
4 Содержание практики
Работа на предприятии.
"""


def test_practice_fields_stop_at_labels_and_keep_wrapped_type():
    data = sections.extract("practice", PRACTICE)
    assert data["practice_kind"] == "Производственная практика"
    assert data["practice_type"] == "Производственная практика: технологическая (проектно-технологическая) практика"
    assert data["method"] == ""
    assert data.get("form") == ""


def test_practice_method_and_form_are_separate_even_on_one_line():
    text = PRACTICE.replace("Способ проведения –\nФорма проведения –",
                            "Способ проведения – выездная Форма проведения – дискретная")
    data = sections.extract("practice", text)
    assert data["method"] == "выездная"
    assert data["form"] == "дискретная"


def test_legacy_practice_display_repairs_saved_section():
    old = {"practice_kind": "Производственная практика", "practice_type": "Производственная практика: технологическая (проектно-",
           "method": "Форма проведения –", "semesters": [6], "ze": 6, "weeks": 4,
           "sections": {"kind": {"text": PRACTICE.split("\n",1)[1].split("2 Перечень")[0]}}}
    facts = dict(doc_view.build("practice", old)["facts"])
    assert facts["Тип практики"] == "Технологическая (проектно-технологическая) практика"
    assert "Способ проведения" not in facts and "Форма проведения" not in facts
    assert facts["Семестр"] == "6" and facts["Объём"] == "6 з. е. · 4 недели"


def test_semester_hours_and_weekly_hours_use_unrounded_total():
    up = {"semesters": [{"number": 1, "weeks": 14}], "rows": [
        {"index": "Б1.О.01", "name": "М/еБ-программирование", "semesters": {"1": {"lec": 296}}, "control": {}}]}
    s = plan_view.build(up)["semesters"][0]
    assert s["items"][0]["name"] == "Web-программирование"
    assert s["pairs"] == 148 and s["hours"] == 222
    assert s["per_week"] == 10.6 and s["hours_per_week"] == pytest.approx(222 / 14)


def test_program_display_removes_area_codes_but_keeps_meaningful_numbers(tmp_path):
    app = create_app(demo_settings(tmp_path), start_worker=False)
    with app.state.db.Session() as ses:
        prog = Program(id="clean-op", level="bachelor", level_title="Бакалавриат", year=2026,
                       code="08.03.01", profile="Водоснабжение и водоотведение", form="Очная",
                       source_url="https://www.istu.edu/test")
        ses.add(prog)
        ses.add_all([Document(id=1,program_id=prog.id,doc_type="oop",title="ООП",url="https://www.istu.edu/oop.pdf"),
                     Document(id=2,program_id=prog.id,doc_type="up",title="УП",url="https://www.istu.edu/up.pdf")])
        ses.flush()
        areas=["16 Строительство (объек- тов, техниче- ской эксплуатации и ма- териалов)",
               "40. Сквозные виды профессиональной деятельности", "06 Связь и технологии 5G"]
        ses.add_all([JsonVersion(document_id=1,kind="final",version=1,data={"data":{"areas":areas}}),
                     JsonVersion(document_id=2,kind="final",version=1,data={"data":{
                         "semesters":[{"number":1,"weeks":14}],"rows":[{"index":"Б1.О.01","name":"М/еБ-программирование",
                         "semesters":{"1":{"lec":296}},"control":{},"ze":4}]}})])
        ses.flush()
        prog.page=build(ses,prog)
        assert prog.page["op"]["areas"] == ["Строительство (объектов, технической эксплуатации и материалов)",
            "Сквозные виды профессиональной деятельности","Связь и технологии 5G"]
        ses.commit()
    with TestClient(app) as client:
        soup=BeautifulSoup(client.get("/op/clean-op").text,"html.parser")
        load=soup.select_one(".app-load").get_text(" ",strip=True)
        assert "222 ч" in load and "15 ч 51 мин" in load and "10,6" in load
        assert "Web-программирование" in soup.get_text() and "М/еБ" not in soup.get_text()
        card=BeautifulSoup(client.get('/op/clean-op/discipline?index=Б1.О.01').text,'html.parser')
        assert card.h1.get_text(strip=True)=="Web-программирование"


def test_text_cleanup_preserves_real_compounds_and_coordination():
    from robot_op.extract.util import unwrap_text
    assert unwrap_text("северо-\nвосточный") == "северо-восточный"
    assert unwrap_text("онлайн-\nобучение") == "онлайн-обучение"
    assert unwrap_text("научно- и производственно-технологическая деятельность") == "научно- и производственно-технологическая деятельность"
    assert unwrap_text("объек- тов, техниче- ской эксплуатации и ма- териалов") == "объектов, технической эксплуатации и материалов"


def test_partial_practice_source_does_not_erase_valid_structured_fields():
    data={"practice_kind":"Учебная практика","method":"Стационарная","form":"Дискретная",
          "sections":{"kind":{"text":"Вид практики – Учебная практика\nТип практики – Технологическая"}}}
    facts=dict(doc_view.build("practice",data)["facts"])
    assert facts["Способ проведения"] == "Стационарная"
    assert facts["Форма проведения"] == "Дискретная"
    data["sections"]["kind"]["text"] += "\nСпособ проведения –\nФорма проведения –"
    facts=dict(doc_view.build("practice",data)["facts"])
    assert "Способ проведения" not in facts and "Форма проведения" not in facts


def test_practice_label_can_wrap_before_its_separator():
    data=sections.extract("practice",PRACTICE.replace("Способ проведения –", "Способ проведения\n– Стационарная"))
    assert data["method"] == "Стационарная"
    assert data["practice_type"] == "Производственная практика: технологическая (проектно-технологическая) практика"


def test_clock_hours_render_whole_and_half_pairs_and_weekly_average():
    from robot_op.web.app import clock_hours
    assert clock_hours(1.5) == "1 ч 30 мин"
    assert clock_hours(.75) == "45 мин"
    assert clock_hours(210) == "210 ч"
    assert clock_hours(222/14) == "15 ч 51 мин"
