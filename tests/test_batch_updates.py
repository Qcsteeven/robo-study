from unittest.mock import patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import demo_settings
from robot_op.models import Document, Program, Run, RunItem
from robot_op.web.app import create_app


def test_multi_level_form_and_submission(tmp_path):
    app = create_app(demo_settings(tmp_path, ROBOT_APP_DEMO="1"), start_worker=False)
    with TestClient(app) as client:
        html = client.get("/run?level=bachelor&level=master").text
        soup = BeautifulSoup(html, "html.parser")
        assert not soup.select('input[name="force"]')
        assert not soup.find("h2", string="Обновить данные с сайта ИРНИТУ")
        assert "Получить документы образовательных программ последнего года набора" in html
        assert len(soup.select('input[name="level"][type="checkbox"][checked]')) == 2
        assert soup.select('input[name="programs__bachelor"]')
        assert soup.select('input[name="programs__master"]')
        response = client.post("/run", data={
            "level": ["bachelor", "master"],
            "programs__bachelor": ["09.03.01-isoiu-2026-och"],
            "programs__master": ["09.04.01-iiis-2026-och"], "force": "1"
        }, follow_redirects=False)
        assert response.status_code == 303
        with app.state.db.Session() as ses:
            run = ses.scalar(select(Run).order_by(Run.id.desc()))
            assert len(run.params["selections"]) == 2
            assert not run.params.get("force")
            assert {s["year"] for s in run.params["selections"]} == {2026}
        robot = app.state.robot
        with patch.object(robot, "_process"):
            robot.execute(run.id)
        with app.state.db.Session() as ses:
            assert {p.level for p in ses.scalars(select(Program))} == {"bachelor", "master"}
            assert ses.get(Run, run.id).status == "finished"


def test_empty_selection_does_not_start_run(tmp_path):
    app = create_app(demo_settings(tmp_path, ROBOT_APP_DEMO="1"), start_worker=False)
    with TestClient(app) as client:
        assert client.post("/run", data={"level": ["master", "bachelor"]}, follow_redirects=False).status_code == 303
        with app.state.db.Session() as ses:
            assert ses.scalar(select(Run)) is None


def test_remote_change_refreshes_whole_program(robot):
    # Exercise the orchestration without OCR: unchanged bytes reuse saved parsing,
    # one changed document refreshes every document in that program only.
    with robot.db.Session() as ses:
        for key in ("a", "b"):
            ses.add(Program(id=key, profile=key, level="master", level_title="Магистратура", year=2026))
        ses.flush()
        docs = [Document(program_id=p, doc_type="rpd", title=str(i), url=f"http://demo.local/{p}{i}.pdf")
                for p in ("a", "b") for i in range(2)]
        ses.add_all(docs)
        ses.flush()
        ids = [d.id for d in docs]
        run = Run(status="running", params={})
        ses.add(run); ses.flush()
        ses.add_all([RunItem(run_id=run.id, program_id=d.program_id, document_id=d.id,
                            doc_type=d.doc_type, title=d.title, url=d.url) for d in docs])
        ses.commit()
        rid = run.id
    seen = []
    def download(run_id, doc, client):
        return b"pdf", doc.id == ids[0]
    def document(run_id, item_id, client, force):
        with robot.db.Session() as ses:
            d = ses.get(Document, ses.get(RunItem, item_id).document_id)
            seen.append((d.program_id, force))
        return "processed", ""
    with patch.object(robot, "_download", side_effect=download), \
         patch.object(robot, "_document", side_effect=document), \
         patch.object(robot, "_update_page"):
        robot._process(rid, {}, None)
    assert seen == [("a", True), ("a", True), ("b", False), ("b", False)]
