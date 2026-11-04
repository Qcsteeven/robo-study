from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.testclient import TestClient

from robot_op.export_pages import export_site


def static_source(extra_link=""):
    app = FastAPI()
    @app.get("/programs")
    def programs(level: str = ""):
        return HTMLResponse('<html><head><title>Программы</title></head><body><main><h1>' + (level or "Каталог") + '</h1>'
            '<a href="/programs?level=master">Магистратура</a>'
            '<a href="/op/test/discipline?index=Б1.О.01">Первая</a>'
            '<a href="/op/test/discipline?index=Б1.О.02">Вторая</a>'
            '<a href="/run">Обновить данные</a><a class="app-theme" href="/theme?mode=dark">Тёмная тема</a>'
            '<link rel="stylesheet" href="http://testserver/static/css/app.css?v=1">'
            '<a href="/api/documents/1/json?kind=final">JSON</a>' + extra_link + '</main></body></html>')
    @app.get("/")
    def home():
        return programs()
    @app.get("/op/test/discipline")
    def discipline(index: str):
        return HTMLResponse('<html><head><title>Программы</title></head><body><main><h1>' + index + '</h1><a href="/programs">Каталог</a>'
                            '<a href="https://www.istu.edu/source.pdf">PDF</a></body></html>')
    @app.get("/api/documents/1/json")
    def doc_json(kind: str):
        return JSONResponse({"kind": kind, "data": {"qualification": "Магистр"}})
    @app.get("/run")
    def run():
        raise AssertionError("Exporter must never request the live robot page")
    @app.get("/static/css/app.css")
    def css():
        return Response("body {color: black}", media_type="text/css")
    return TestClient(app)


def local_file(output, href):
    path = unquote(urlsplit(href).path)
    assert path.startswith("/robo-study/")
    path = output / path.removeprefix("/robo-study/")
    return path / "index.html" if path.suffix == "" else path


def test_static_export_preserves_distinct_queries_and_downloads(tmp_path):
    output = tmp_path / "site"
    report = export_site(static_source(), output, "/robo-study/", ["/api/documents/1/json?kind=primary"])
    soup = BeautifulSoup((output / "index.html").read_text(), "html.parser")
    first = soup.find("a", string="Первая")["href"]
    second = soup.find("a", string="Вторая")["href"]
    assert first != second
    assert "Б1.О.01" in local_file(output, first).read_text()
    assert "Б1.О.02" in local_file(output, second).read_text()
    level = soup.find("a", string="Магистратура")["href"]
    assert "<h1>master</h1>" in local_file(output, level).read_text()
    for html in output.rglob("*.html"):
        parsed = BeautifulSoup(html.read_text(), "html.parser")
        assert not parsed.select("form")
        for el in parsed.select("[href], [src]"):
            href = el.get("href") or el.get("src")
            if href.startswith("/robo-study/"):
                assert local_file(output, href).is_file(), (html, href)
            assert "testserver" not in href and "localhost" not in href
    json_link = soup.find("a", string="JSON")["href"]
    assert '"qualification"' in local_file(output, json_link).read_text()
    assert (output / ".nojekyll").exists()
    assert report["pages"] >= 5
    update = local_file(output, soup.find("a", string="Обновить данные")["href"]).read_text()
    assert "локальн" in update.lower() and "GitHub Pages" in update
    assert soup.select_one("[data-static-snapshot]")
    assert soup.select_one("a[data-theme-toggle]")


def test_export_rejects_existing_nonempty_output(tmp_path):
    (tmp_path / "keep.txt").write_text("user data")
    with pytest.raises(ValueError, match="empty"):
        export_site(static_source(), tmp_path, "/robo-study/")
    assert (tmp_path / "keep.txt").read_text() == "user data"


def test_export_rejects_unexpected_backend_route(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        export_site(static_source('<a href="/api/runs/1">Run</a>'), tmp_path / "site", "/robo-study/")
