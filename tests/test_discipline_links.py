from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from conftest import demo_settings
from robot_op.models import Document, JsonVersion, Program
from robot_op.robot.page import build
from robot_op.web.app import create_app


def row(index, name, semesters):
    return {"index": index, "name": name, "ze": 4, "is_module": index == "Б1.В.ДВ.01",
            "hours": {"plan": 144, "sr": 54}, "checks": [],
            "semesters": {str(s): {"lec": 30, "lab": 60, "sr": 54} for s in semesters},
            "control": {"exam": semesters[-1:]}}


@pytest.fixture
def discipline_app(tmp_path):
    app = create_app(demo_settings(tmp_path), start_worker=False)
    with app.state.db.Session() as ses:
        prog = Program(id="test-op", level="bachelor", level_title="Бакалавриат", year=2026,
                       code="09.03.01", profile="Тестовая программа", form="Очная",
                       source_url="https://www.istu.edu/test")
        ses.add(prog)
        ses.add_all([
            Document(id=1, program_id=prog.id, doc_type="up", title="Учебный план", url="https://www.istu.edu/up.pdf"),
            Document(id=2, program_id=prog.id, doc_type="rpd", title="РПД Математика", url="https://www.istu.edu/math.pdf"),
            Document(id=3, program_id=prog.id, doc_type="rpd", title="Web-программирование", url="https://www.istu.edu/web.pdf"),
        ])
        ses.flush()
        ses.add_all([
            JsonVersion(document_id=1, kind="final", version=1, data={"document": {"pages": 1}, "data": {
                "semesters": [{"number": 1, "weeks": 16}, {"number": 2, "weeks": 16}],
                "rows": [row("Б1.О.01", "Математика", [1]), row("Б1.О.02", "Информатика", [1, 2]),
                         row("Б1.О.03", "М/еБ-программирование", [2]),
                         row("Б1.В.ДВ.01", "Дисциплины по выбору", [2]),
                         row("Б1.В.ДВ.01.01", "Обработка данных", [2]),
                         row("Б1.В.ДВ.01.02", "Математика", [2])]}}),
            JsonVersion(document_id=2, kind="final", version=1, data={"document": {"pages": 1}, "data": {"discipline": "Математика"}}),
        ])
        ses.flush()
        prog.page = build(ses, prog)
        ses.commit()
    return app


def test_every_curriculum_position_has_a_working_link(discipline_app):
    with TestClient(discipline_app) as client:
        page = BeautifulSoup(client.get("/op/test-op").text, "html.parser")
        names = page.select(".app-semtable__name")
        assert names and all(n.find("a", href=True) is not None for n in names)
        math = next(n.find("a") for n in names if n.get_text(strip=True) == "Математика")
        assert math["href"] == "/op/test-op/doc/2"
        web = next(n.find("a") for n in names if n.get_text(strip=True) == "М/еБ-программирование")
        assert web["href"] == "/op/test-op/doc/3"
        for name in names:
            assert client.get(name.find("a")["href"]).status_code == 200
        info = next(n.find("a") for n in names if n.get_text(strip=True) == "Информатика")
        assert parse_qs(urlparse(info["href"]).query)["index"] == ["Б1.О.02"]
        card = BeautifulSoup(client.get(info["href"]).text, "html.parser")
        assert card.find("h1").get_text(strip=True) == "Информатика"
        assert "144 ч" in card.get_text() and "4 з. е." in card.get_text()
        assert card.select_one('a[href="https://www.istu.edu/up.pdf"]') is not None


def test_elective_card_lists_each_option(discipline_app):
    with TestClient(discipline_app) as client:
        page = BeautifulSoup(client.get("/op/test-op").text, "html.parser")
        choice = next(n.find("a") for n in page.select(".app-semtable__name") if "На выбор:" in n.get_text())
        assert parse_qs(urlparse(choice["href"]).query)["index"] == ["Б1.В.ДВ.01"]
        card = BeautifulSoup(client.get(choice["href"]).text, "html.parser")
        options = card.select(".app-discipline-options a")
        assert [a.get_text(strip=True) for a in options] == ["Обработка данных", "Математика"]
        assert options[1]["href"] == "/op/test-op/doc/2"
        assert all(client.get(a["href"]).status_code == 200 for a in options)


def test_unknown_curriculum_entry_is_not_found(discipline_app):
    with TestClient(discipline_app) as client:
        assert client.get("/op/test-op/discipline", params={"index": "missing"}).status_code == 404
        assert client.get("/op/other/discipline", params={"index": "Б1.О.02"}).status_code == 404
