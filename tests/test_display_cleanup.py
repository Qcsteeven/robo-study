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
                     Document(id=2,program_id=prog.id,doc_type="up",title="УП",url="https://www.istu.edu/up.pdf"),
                     Document(id=3, program_id=prog.id, doc_type="gia", title="ГИА", url="https://www.istu.edu/gia.pdf"),
                     Document(id=4, program_id=prog.id, doc_type="kug", title="КУГ", url="https://www.istu.edu/kug.pdf")])
        ses.flush()
        areas=["16 Строительство (объек- тов, техниче- ской эксплуатации и ма- териалов)",
               "40. Сквозные виды профессиональной деятельности", "06 Связь и технологии 5G"]
        ses.add_all([JsonVersion(document_id=1,kind="final",version=1,data={"data":{"areas":areas}}),
                     JsonVersion(document_id=2,kind="final",version=1,data={"data":{
                         "semesters": [{"number": 1, "weeks": 14}, {"number": 2, "weeks": 0}],
                         "rows": [{"index":"Б1.О.01", "name":"М/еБ-программирование",
                                   "semesters":{"1":{"lec":296}, "2":{"lec":32}}, "control":{}, "ze":4}]}}),
                     JsonVersion(document_id=3, kind="final", version=1, data={
                         "summary": "Аттестация проходит в форме защиты выпускной работы.",
                         "data": {"forms": ["Защита ВКР"], "ze": 6}})])
        ses.flush()
        prog.page = build(ses, prog, teacher_directory={
            "web-программирование": [
                {"name": "Иванов Иван Иванович", "url": "https://www.istu.edu/sveden/employees"},
            ],
        })
        assert prog.page["op"]["areas"] == ["Строительство (объектов, технической эксплуатации и материалов)",
            "Сквозные виды профессиональной деятельности","Связь и технологии 5G"]
        ses.commit()
    with TestClient(app) as client:
        soup=BeautifulSoup(client.get("/op/clean-op").text,"html.parser")
        load=soup.select_one(".app-load").get_text(" ",strip=True)
        assert "Пар за семестр" in load and "222 ч" in load
        assert "Пар за учебный год" in load and "246 ч" in load
        table = soup.select_one(".app-semtable")
        assert table.thead and table.tfoot.th.get_text(strip=True) == "Итого в неделю"
        assert "Предполагаемый преподаватель" in table.thead.get_text()
        teacher = table.select_one('tbody a[href="https://www.istu.edu/sveden/employees"]')
        assert teacher.get_text(strip=True) == "Иванов Иван Иванович"
        assert table.select_one("tbody .app-semtable__sum").get_text(strip=True) == "10,5"
        assert table.select_one("tfoot .app-semtable__sum").get_text(strip=True) == "10,5"
        assert soup.select("tbody .app-semtable__sum")[1].get_text(strip=True) == "—"
        assert soup.select("tfoot .app-semtable__sum")[1].get_text(strip=True) == "—"
        gia = soup.select_one("#gia")
        assert gia.select_one(".app-prose").get_text(strip=True) == "Аттестация проходит в форме защиты выпускной работы."
        assert not gia.select("a, .ir-info")
        assert "Web-программирование" in soup.get_text() and "М/еБ" not in soup.get_text()
        assert soup.select_one("#kug .app-empty").get_text(strip=True) == "Календарный учебный график пока не опубликован."
        assert soup.select_one('#docs a[href="https://www.istu.edu/kug.pdf"]')
        document = BeautifulSoup(client.get("/op/clean-op/doc/4").text, "html.parser")
        assert document.select_one(".app-content .app-empty").get_text(strip=True) == "Сведения о документе пока не опубликованы."
        assert document.select_one('.app-aside a[href="https://www.istu.edu/kug.pdf"]')
        card=BeautifulSoup(client.get('/op/clean-op/discipline?index=Б1.О.01').text,'html.parser')
        assert card.h1.get_text(strip=True)=="Web-программирование"
        assert "Рабочая программа пока не опубликована." in card.select_one(".app-content").get_text()
        assert "Пар в неделю" in card.select_one("thead").get_text()
        assert card.select_one("tbody tr td").get_text(strip=True) == "10,5"
    with app.state.db.Session() as ses:
        prog = ses.get(Program, "clean-op")
        prog.page = dict(prog.page, plan=None, gia={"summary": [
            "Генеративные языковые модели не обладают собственным мнением — их ответы являются обобщением информации.",
            "Чтобы избежать ошибок и неправильного толкования, разговоры на чувствительные темы могут быть ограничены .",
        ]})
        ses.commit()
    with TestClient(app) as client:
        soup = BeautifulSoup(client.get("/op/clean-op").text, "html.parser")
        assert soup.select_one("#up .app-empty").get_text(strip=True) == "Учебный план пока не опубликован."
        gia = soup.select_one("#gia .app-empty")
        assert gia.get_text(strip=True) == "Описание государственной итоговой аттестации пока не опубликовано."


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
