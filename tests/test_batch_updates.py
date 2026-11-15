from dataclasses import replace
from json import loads
from threading import Event
from unittest.mock import patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import demo_settings
from demo_site import render
from robot_op.models import Document, Program, Run, RunItem
from robot_op.robot.http import SiteError
from robot_op.web.app import create_app


def test_multi_level_form_and_submission(tmp_path):
    app = create_app(demo_settings(tmp_path, ROBOT_APP_DEMO="1"), start_worker=False)
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/run"
        landing = BeautifulSoup(client.get("/", follow_redirects=True).text, "html.parser")
        assert landing.h1.get_text(strip=True) == "Образовательные программы"
        assert landing.select_one("[data-catalog-url]")
        search = landing.select_one('input[type="search"]')
        assert search and landing.select_one(f'label[for="{search["id"]}"]')
        assert [element.get_text(strip=True) for element in
                landing.select("[data-catalog-filters] legend, [data-catalog-filters] label")] == [
                    "Год набора", "Уровень образования", "Поиск по программам",
                ]
        catalog = BeautifulSoup(client.get("/programs/catalog").text, "html.parser")
        assert catalog.select_one('form input[value="09.03.01-isoiu-2026-och"]')
        card = catalog.select_one('article:has(input[value="09.04.01-iiis-2026-och"])')
        assert "09.04.01" in card["data-program-search"]
        assert "Искусственный интеллект" in card["data-program-search"]
        group = card.find_parent("section")
        assert group["data-catalog-year"] == "2026" and group["data-catalog-level"] == "master"
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


def test_only_new_programs_can_be_loaded(tmp_path):
    app = create_app(demo_settings(tmp_path, ROBOT_APP_DEMO="1"), start_worker=False)
    saved = "09.03.01-isoiu-2026-och"
    fresh = "09.04.01-iiis-2026-och"
    with app.state.db.Session() as ses:
        ses.add(Program(id=saved, level="bachelor", level_title="Бакалавриат", year=2026,
                        page={"op": {"profile": "Загруженная ОП"}}))
        ses.commit()

    with TestClient(app) as client:
        html = BeautifulSoup(client.get("/programs/catalog").text, "html.parser")
        assert html.select_one(f'a[href="/op/{saved}"]')
        assert not html.select(f'input[value="{saved}"]')
        assert html.select_one(f'form[action="/run"] input[value="{fresh}"]')
        with patch.object(app.state.robot, "levels", side_effect=AssertionError("Opening the catalog must not wait for the source")):
            assert client.get("/programs").status_code == 200
        options = client.get("/api/site/programs?level=bachelor&new_only=true").json()
        assert saved not in {option["key"] for option in options}

        assert client.post("/api/runs", json={"level": "bachelor", "programs": [saved], "force": True}).status_code == 409
        client.post("/run", data={"level": "bachelor", "programs__bachelor": saved})
        with app.state.db.Session() as ses:
            assert ses.scalar(select(Run)) is None

        load = client.post("/run", data={"level": "master", "program_id": fresh}, follow_redirects=False)
        assert load.status_code == 303
        assert client.get(load.headers["location"]).status_code == 200
        repeat = client.post("/run", data={"level": "master", "program_id": fresh}, follow_redirects=False)
        assert repeat.headers["location"] == load.headers["location"]
        assert client.post("/api/runs", json={"level": "master", "programs": [fresh]}).status_code == 409
        with app.state.db.Session() as ses:
            run, = ses.scalars(select(Run)).all()
            assert run.params["programs"] == [fresh]
            assert run.params["only_new"]


def test_catalog_preloads_at_startup_and_survives_source_outage(tmp_path):
    settings = demo_settings(tmp_path, ROBOT_APP_DEMO="1")
    settings.site.base_url = "http://localhost:8000/demo-site"
    app = create_app(settings)
    called = Event()
    original = app.state.robot.catalog

    def fetch_catalog(*args, **kwargs):
        called.set()
        return original(*args, **kwargs)

    with patch.object(app.state.robot, "catalog", side_effect=fetch_catalog):
        with TestClient(app) as client:
            assert called.wait(2), "The catalog should load without a browser request"
            assert client.get("/programs/catalog").status_code == 200
            html = BeautifulSoup(client.get("/run").text, "html.parser")
            assert html.select_one('form[action="/run"] input[value="09.04.01-iiis-2026-och"]')
            assert html.select_one('form[action="/run"] input[value="09.03.01-isoiu-2026-och"]')
            with app.state.db.Session() as ses:
                assert ses.scalar(select(Run)) is None

    offline = create_app(settings)
    with patch.object(offline.state.robot, "catalog", side_effect=SiteError("Источник недоступен")):
        with TestClient(offline) as client:
            html = BeautifulSoup(client.get("/programs/catalog").text, "html.parser")
            assert html.select_one('form[action="/run"] input[value="09.04.01-iiis-2026-och"]')
            assert "Сайт ИРНИТУ не передал полный список" not in html.get_text()
            years = loads(html.select_one("[data-catalog-years]").string)
            assert set(years["2020"]) == {"specialist"}


def test_catalog_exposes_all_source_years_and_loads_selected_cohort(tmp_path):
    app = create_app(demo_settings(tmp_path, ROBOT_APP_DEMO="1"), start_worker=False)
    previous = replace(render.PROGRAMS[0], year=2024)
    with patch.object(render, "PROGRAMS", [*render.PROGRAMS, previous]), TestClient(app) as client:
        initial = BeautifulSoup(client.get("/programs/catalog").text, "html.parser")
        availability = loads(initial.select_one("[data-catalog-years]").string)
        assert set(availability) == {str(year) for year in range(2020, 2027)}
        assert set(availability["2020"]) == {"specialist"}
        assert "master" in availability["2024"]
        assert not initial.select('[data-catalog-year="2024"]')

        selected = BeautifulSoup(client.get("/programs/catalog?year=2024").text, "html.parser")
        groups = selected.select(".app-cards-group")
        assert groups and {group["data-catalog-year"] for group in groups} == {"2024"}
        assert selected.select_one('form input[name="program_id"][value$="-2024-och"]')
        latest = BeautifulSoup(client.get("/programs/catalog?year=2026").text, "html.parser")
        assert {group["data-catalog-year"] for group in latest.select(".app-cards-group")} == {"2026"}
