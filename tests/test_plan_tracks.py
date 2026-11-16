from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from robot_op.models import Document, JsonVersion, Program
from robot_op.robot import plan_view
from robot_op.robot.page import build
from robot_op.web.app import create_app


def row(index, name, hours=0, *, module=False, semester=3):
    return {"index": index, "name": name, "is_module": module, "ze": 3,
            "hours": {"plan": 108, "aud": hours},
            "semesters": {str(semester): {"lec": hours}}, "control": {"exam": [semester]}}


def curriculum_rows():
    root = "Б1.В.ДВ.01"
    practice = "Б2.В.ДВ.01"
    return [
        row("Б1.Б.01", "Общая дисциплина", 10),
        row(root, "Индивидуальная образовательная траектория", 120, module=True),
        row(root + ".01", "Прикладной ИИ", 60, module=True),
        row(root + ".01.01", "Анализ данных", 20),
        row(root + ".01.02", "Методы моделирования", 14),
        row(root + ".01.ДВ.01", "Дисциплины по выбору", 12, module=True),
        row(root + ".01.ДВ.01.01", "Компьютерное зрение", 12),
        row(root + ".01.ДВ.01.02", "Обработка изображений", 12),
        row(root + ".02", "Промышленный интернет вещей", 60, module=True),
        row(root + ".02.01", "Промышленные сети", 30),
        row(root + ".02.02", "Методы моделирования", 18),
        row(root + ".02.03", "Методы оптимизации", 16),
        row(practice, "Индивидуальная образовательная траектория", module=True),
        row(practice + ".01", "Промышленный интернет вещей", module=True),
        row(practice + ".01.01(П)", "Исследовательская практика"),
        row(practice + ".02", "Прикладной ИИ", module=True),
        row(practice + ".02.01(П)", "Технологическая практика"),
    ]


def test_tracks_include_common_rows_and_count_each_choice_once():
    plan = plan_view.build({"rows": curriculum_rows(), "semesters": [{"number": 3, "weeks": 14}]})
    ai, iot = plan["tracks"]
    assert [ai["name"], iot["name"]] == ["Прикладной ИИ", "Промышленный интернет вещей"]
    ai_sem, iot_sem = ai["semesters"][2], iot["semesters"][2]
    assert ai_sem["pairs"] == 28
    assert iot_sem["pairs"] == 37
    ai_items = {item["name"]: item for item in ai_sem["items"]}
    assert set(ai_items) == {"Общая дисциплина", "Анализ данных", "Методы моделирования",
                             "Компьютерное зрение / Обработка изображений", "Технологическая практика"}
    elective = ai_items["Компьютерное зрение / Обработка изображений"]
    assert [o["name"] for o in elective["options"]] == ["Компьютерное зрение", "Обработка изображений"]
    assert {i["name"] for i in iot_sem["items"]} == {"Общая дисциплина", "Промышленные сети",
                                                     "Методы моделирования", "Методы оптимизации",
                                                     "Исследовательская практика"}
    assert ai["total_ze"] == iot["total_ze"] == 15


def test_ordinary_elective_and_single_trajectory_need_no_switcher():
    root = "Б1.В.ДВ.01"
    child = row(root + ".01", "Трек по анализу данных", module=True)
    items = [child, row(root + ".01.01", "Анализ данных", 20)]
    plan = plan_view.build({"rows": [row(root, "Индивидуальная образовательная траектория",
                                          20, module=True), *items]})
    assert not plan.get("tracks")
    assert [i["name"] for i in plan["semesters"][2]["items"]] == ["Анализ данных"]

    ordinary = [row(root, "Дисциплины по выбору", 10, module=True),
                row(root + ".01", "История", 10), row(root + ".02", "Право", 10)]
    plan = plan_view.build({"rows": ordinary})
    assert not plan.get("tracks")
    assert plan["semesters"][2]["items"][0]["name"] == "История / Право"


def test_saved_program_displays_tracks_and_links_to_the_matching_rpds(settings):
    app = create_app(settings, start_worker=False)
    with app.state.db.Session() as ses:
        for key in ("tracked", "plain"):
            prog = Program(id=key, level="master", level_title="Магистратура", year=2026,
                           code="09.04.02", profile=key, form="Очная",
                           source_url="https://www.istu.edu/program")
            ses.add(prog)
            ses.flush()
            up = Document(program_id=key, doc_type="up", title="Учебный план",
                          url="https://www.istu.edu/up.pdf")
            ses.add(up)
            ses.flush()
            data = {"rows": [row("Б1.Б.01", "Общая дисциплина", 10)]}
            ses.add(JsonVersion(document_id=up.id, kind="final", version=1, data={"data": data}))
            ses.flush()
            prog.page = build(ses, prog)
            prog.page["plan"].pop("tracks", None)
            if key == "tracked":
                ses.add(JsonVersion(document_id=up.id, kind="final", version=2,
                                    data={"data": {"rows": curriculum_rows()}}))
        for doc_id, hours in ((21, 18), (22, 14)):
            doc = Document(id=doc_id, program_id="tracked", doc_type="rpd", title="Методы моделирования",
                           url=f"https://www.istu.edu/{doc_id}.pdf")
            ses.add(doc)
            ses.flush()
            ses.add(JsonVersion(document_id=doc_id, kind="final", version=1, data={"data": {
                "discipline": doc.title, "semesters": [3], "ze": 3, "hours": {"total": 108, "aud": hours},
            }}))
        ses.commit()

    with TestClient(app) as client:
        response = client.get("/op/tracked")
        assert response.status_code == 200
        html = BeautifulSoup(response.text, "html.parser")
        assert [button.get_text(strip=True) for button in html.select("[data-track-choice]")] == [
            "Прикладной ИИ", "Промышленный интернет вещей"]
        assert not BeautifulSoup(client.get("/op/plain").text, "html.parser").select("[data-track-choice]")
        assert len(client.get("/api/programs/tracked").json()["plan"]["tracks"]) == 2
        urls = {}
        for panel in html.select("[data-track-plan]"):
            for a in panel.select(".app-semtable a"):
                name = a.get_text(strip=True)
                if name == "Методы моделирования":
                    urls[panel["data-track-plan"]] = a["href"]
                if name == "Промышленные сети":
                    assert parse_qs(urlsplit(a["href"]).query)["index"] == ["Б1.В.ДВ.01.02.01"]
                    assert client.get(a["href"]).status_code == 200
        assert urls == {"0": "/op/tracked/doc/22", "1": "/op/tracked/doc/21"}
