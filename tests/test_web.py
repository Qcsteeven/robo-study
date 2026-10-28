import time

from fastapi.testclient import TestClient

from conftest import demo_settings, needs_tesseract
from robot_op.web.app import create_app

pytestmark = needs_tesseract


def test_web_flow(tmp_path):
    settings = demo_settings(tmp_path, ROBOT_APP_DEMO="1")
    settings.site.base_url = "http://localhost:8000/demo-site"
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/run").status_code == 200
        page = c.get("/run?level=master&year=2024").text
        assert "Искусственный интеллект в информационных системах" in page
        r = c.post("/run", data={"level": "master", "year": "2024", "all": "1"}, follow_redirects=False)
        assert r.status_code == 303
        run_url = r.headers["location"]
        run_id = int(run_url.rsplit("/", 1)[1])
        for _ in range(240):
            if c.get(f"/api/runs/{run_id}").json()["status"] in ("finished", "failed"):
                break
            time.sleep(0.5)
        assert c.get(f"/api/runs/{run_id}").json()["status"] == "finished"
        for url in (run_url, "/reports", "/programs", "/log", f"/log?run={run_id}&level=WARNING", f"/api/runs/{run_id}/report",
                    "/demo-site/sveden/education/"):
            assert c.get(url).status_code == 200, url
        op = c.get("/op/09.04.01-iiis-2024-och")
        assert op.status_code == 200
        assert "Учебный план" in op.text and "Глубокое обучение" in op.text and "app-kug" in op.text
        docs = c.get("/api/programs/09.04.01-iiis-2024-och/documents").json()
        assert all(c.get(f"/op/09.04.01-iiis-2024-och/doc/{d['id']}").status_code == 200 for d in docs)
        assert c.get(f"/api/documents/{docs[0]['id']}/json?kind=primary").json()["schema"] == "robot-op/primary/1"
        assert c.get("/op/unknown").status_code == 404
        assert c.get("/theme?mode=dark&next=/programs", follow_redirects=False).cookies.get("theme") == "dark"
