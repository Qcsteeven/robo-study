from hashlib import sha256
from urllib.parse import urlsplit

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from demo_site.render import dummy_pdf
from robot_op.config import ROOT, SiteSettings, load_settings
from robot_op.models import Document, JsonVersion, Program, Run
from robot_op.robot.http import SiteClient, SiteError
from robot_op.web.app import create_app


DOCUMENT_TITLE = "Программа курса <2026> & приложения"


@pytest.fixture
def web_app(tmp_path):
    settings = load_settings(env={
        "ROBOT_APP_DATA_DIR": str(tmp_path),
        "ROBOT_APP_DATABASE_URL": f"sqlite:///{tmp_path / 'robot.db'}",
    })

    def unexpected_request(request):
        pytest.fail(f"Unexpected outbound request to {request.url.host}")

    app = create_app(settings, transport=httpx.MockTransport(unexpected_request), start_worker=False)
    content = dummy_pdf("Course description")
    digest = sha256(content).hexdigest()
    path = app.state.robot.docs_dir / f"{digest}.pdf"
    path.write_bytes(content)

    with app.state.db.Session() as session:
        session.add_all([
            Program(id="course-a", level="bachelor", level_title="Бакалавриат", year=2026, code="09.03.01"),
            Program(id="course-b", level="bachelor", level_title="Бакалавриат", year=2026, code="09.03.02"),
        ])
        session.flush()
        session.add(Document(id=1, program_id="course-a", doc_type="rpd", title=DOCUMENT_TITLE,
                             url="https://www.istu.edu/course.pdf", file_path=str(path), sha256=digest))
        session.add(Run(id=1, status="finished"))
        session.flush()
        session.add(JsonVersion(document_id=1, kind="final", version=1, data={
            "schema": "robot-op/final/1", "document": {"pages": 1}, "data": {},
        }))
        session.commit()

    with TestClient(app) as client:
        yield client, path
    app.state.db.engine.dispose()


def test_checked_in_settings_use_https_and_finite_site_limits():
    settings = load_settings(env={})
    base = urlsplit(settings.site.base_url)
    assert base.scheme == "https"
    assert base.hostname in settings.site.allowed_hosts
    assert set(settings.site.allow_http_hosts) <= {"localhost", "127.0.0.1", "::1"}
    assert settings.site.https_only
    assert 0 < settings.site.requests_per_second <= 2
    assert 0 < settings.site.max_document_mb <= 80
    assert not settings.app.demo
    assert settings.llm.provider == "none"


def test_nginx_https_profile_configures_request_limit_and_security_headers():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    directives = {tuple(line.strip().rstrip(";").split()) for line in config.splitlines()}
    assert ("listen", "443", "ssl") in directives
    assert ("client_max_body_size", "1m") in directives
    assert ("ssl_protocols", "TLSv1.2", "TLSv1.3") in directives
    assert ("add_header", "X-Content-Type-Options", "nosniff", "always") in directives
    assert ("add_header", "Strict-Transport-Security", '"max-age=31536000"', "always") in directives


def test_site_accepts_same_host_https_redirect():
    def handler(request):
        if request.url.path == "/catalog":
            return httpx.Response(302, headers={"Location": "/catalog/current"})
        assert request.url == httpx.URL("https://www.istu.edu/catalog/current")
        return httpx.Response(200, text="Каталог")

    client = SiteClient(SiteSettings(requests_per_second=0), transport=httpx.MockTransport(handler))
    try:
        assert client.get("https://www.istu.edu/catalog").text == "Каталог"
    finally:
        client.close()


def test_site_enforces_streaming_document_cap_at_byte_boundary():
    class DocumentBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a" * 512
            yield b"b" * 512

    client = SiteClient(SiteSettings(requests_per_second=0), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=DocumentBody()),
    ))
    try:
        assert len(client.get("https://www.istu.edu/document.pdf", max_bytes=1024).content) == 1024
        with pytest.raises(SiteError, match="Документ больше"):
            client.get("https://www.istu.edu/document.pdf", max_bytes=1023)
    finally:
        client.close()


@pytest.mark.parametrize("url,media_type,disposition,filename", [
    ("/documents/1/pdf", "application/pdf", "attachment", "rpd-1.pdf"),
    ("/api/documents/1/json", "application/json", "inline", "doc-1-final-v1.json"),
    ("/api/runs/1/report", "application/json", "attachment", "report-1.json"),
])
def test_document_and_report_response_metadata(web_app, url, media_type, disposition, filename):
    client, _ = web_app
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] == media_type
    kind, filename_part = response.headers["content-disposition"].split(";", 1)
    assert kind == disposition
    assert filename_part.strip() == f'filename="{filename}"'


def test_document_page_escapes_title_and_respects_program_relationship(web_app):
    client, _ = web_app
    response = client.get("/op/course-a/doc/1")
    assert response.status_code == 200
    assert "Программа курса &lt;2026&gt; &amp; приложения" in response.text
    assert BeautifulSoup(response.text, "html.parser").select_one("h1").get_text(strip=True) == DOCUMENT_TITLE
    assert client.get("/op/course-b/doc/1").status_code == 404
    assert [doc["id"] for doc in client.get("/api/programs/course-a/documents").json()] == [1]
    assert client.get("/api/programs/course-b/documents").json() == []


def test_data_directory_is_separate_from_static_assets(web_app):
    client, pdf_path = web_app
    data_path = client.app.state.settings.data_path
    assert client.get("/static/css/app.css").status_code == 200

    for filename in ("robot.db", "logs/robot.log", f"docs/{pdf_path.name}"):
        assert (data_path / filename).is_file()
        assert client.get(f"/static/{filename}").status_code == 404
