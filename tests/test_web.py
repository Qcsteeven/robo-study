import time

from fastapi.testclient import TestClient

from sqlalchemy import select

from conftest import demo_settings, needs_tesseract
from robot_op.models import Run
from robot_op.web.app import create_app

pytestmark = needs_tesseract


def test_web_flow(tmp_path):
    settings = demo_settings(tmp_path, ROBOT_APP_DEMO="1")
    settings.site.base_url = "http://localhost:8000/demo-site"
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/run").status_code == 200
        page = c.get("/run?level=master").text
        # год набора не выбирается — берётся последний
        assert "Искусственный интеллект в информационных системах" in page and "набор 2026" in page
        assert 'name="year"' not in page
        r = c.post("/run", data={"level": "master", "all": "1"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/run?level=master"
        with app.state.db.Session() as ses:
            run = ses.scalar(select(Run).order_by(Run.id.desc()))
            run_id = run.id
            assert run.params["year"] == 2026
        for _ in range(240):
            if c.get(f"/api/runs/{run_id}").json()["status"] in ("finished", "failed"):
                break
            time.sleep(0.5)
        assert c.get(f"/api/runs/{run_id}").json()["status"] == "finished"
        for url in ("/run", "/programs", "/programs?level=master", f"/api/runs/{run_id}/report", "/demo-site/sveden/education/"):
            assert c.get(url).status_code == 200, url
        # отчёты о запусках и журнал — не для посетителей сайта
        for url in ("/reports", f"/reports/{run_id}", "/log"):
            assert c.get(url).status_code == 404, url
        assert c.get("/", follow_redirects=False).headers["location"] == "/programs"
        op = c.get("/op/09.04.01-iiis-2026-och")
        assert op.status_code == 200
        assert "Учебный план" in op.text and "Глубокое обучение" in op.text and "app-kug" in op.text
        # карусель по семестрам с нагрузкой, без большой таблицы и служебных статусов
        assert op.text.count('class="app-carousel__slide"') == 4 and "1 семестр" in op.text and "Пар за семестр" in op.text
        assert "ir-matrix" not in op.text and "ir-status" not in op.text and "Отчёт о запуске" not in op.text
        # график по дням: шесть строк на курс и строка дат «вс» в шапке
        assert op.text.count('<th scope="row" class="app-kug__day">пн</th>') >= 2 and "app-kug--out" in op.text
        assert 'class="app-shelf app-cat--ai"' in op.text and "Из чего состоит программа" in op.text
        docs = c.get("/api/programs/09.04.01-iiis-2026-och/documents").json()
        assert all(c.get(f"/op/09.04.01-iiis-2026-och/doc/{d['id']}").status_code == 200 for d in docs)
        assert c.get(f"/api/documents/{docs[0]['id']}/json?kind=primary").json()["schema"] == "robot-op/primary/1"
        assert c.get("/op/unknown").status_code == 404
        assert c.get("/theme?mode=dark&next=/programs", follow_redirects=False).cookies.get("theme") == "dark"
