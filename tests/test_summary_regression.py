from collections import deque
import json

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from conftest import DEMO_BASE, demo_settings
from demo_site.site import mock_transport
from robot_op.models import Document, JsonVersion, LogEntry, Program, RunItem
from robot_op.web.app import create_app

PROGRAM_ID = "summary-demo"
DOCUMENT_BASE = f"{DEMO_BASE}/sveden/education/docs/ais"
PRACTICE_SUMMARY = ("На практике студенты проводят эксперименты. "
                    "Затем они готовят публикацию и представляют отчёт.")
RPD_SUMMARY = "Студенты осваивают архитектуры глубоких нейронных сетей и применяют их на занятиях."
OOP_SUMMARY = ("Программа готовит магистров по искусственному интеллекту. "
               "Обучение длится два года. Студенты разрабатывают информационные системы.")
GIA_SUMMARY = ("Аттестация проходит в форме защиты выпускной квалификационной работы. "
               "Её трудоёмкость составляет 216 часов. Выпускник отвечает на вопросы комиссии.")
FOS_SUMMARY = "Комиссия оценивает содержание работы, доклад и ответы на вопросы."
OOP_FACTS = {"qualification": "Магистр", "duration": "2 года", "ze_total": 120}
GIA_FACTS = {"forms": ["Защита выпускной квалификационной работы"], "hours": 216}


def completion(text):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}]})


@pytest.fixture
def summary_app(tmp_path):
    responses = deque()
    requests = []

    def model(request):
        assert request.url.host == "llm.local"
        requests.append(json.loads(request.content))
        if not responses:
            pytest.fail("Unexpected request to the language model")
        return responses.popleft()(request)

    app = create_app(
        demo_settings(tmp_path, ROBOT_APP_DEMO="1", ROBOT_LLM_PROVIDER="openai",
                      ROBOT_LLM_BASE_URL="http://llm.local", ROBOT_LLM_MODEL="test-model"),
        transport=mock_transport(), llm_transport=httpx.MockTransport(model), start_worker=False,
    )
    with app.state.db.Session() as session:
        session.add(Program(id=PROGRAM_ID, level="master", level_title="Магистратура",
                            year=2026, profile="Прикладной искусственный интеллект"))
        session.commit()
    return app, responses, requests


def add_document(app, doc_type, title, path, *, status="queued", data=None, summary=None):
    with app.state.db.Session() as session:
        document = Document(program_id=PROGRAM_ID, doc_type=doc_type, title=title,
                            url=f"{DOCUMENT_BASE}/{path}", status=status)
        session.add(document)
        session.flush()
        if data is not None:
            session.add(JsonVersion(document_id=document.id, kind="final", version=1,
                                    data={"data": data, "status": status, "summary": summary}))
        session.commit()
        return document.id


def process_existing_document(app, document_id):
    robot = app.state.robot
    run_id = robot.create_run("master", 2026, [PROGRAM_ID])
    with app.state.db.Session() as session:
        document = session.get(Document, document_id)
        session.add(RunItem(run_id=run_id, program_id=PROGRAM_ID, document_id=document_id,
                            doc_type=document.doc_type, title=document.title, url=document.url))
        session.commit()
    site = robot.site(run_id)
    try:
        robot._process(run_id, {}, site)
    finally:
        site.close()
    return run_id


def versions(app, document_id):
    with app.state.db.Session() as session:
        return session.scalars(select(JsonVersion).where(JsonVersion.document_id == document_id)
                               .order_by(JsonVersion.id)).all()


def journal_messages(app, run_id, level):
    with app.state.db.Session() as session:
        return session.scalars(select(LogEntry.message).where(LogEntry.run_id == run_id,
                                                              LogEntry.level == level)).all()


def model_failure(kind, request):
    if kind == "timeout":
        raise httpx.ReadTimeout("simulated provider timeout", request=request)
    if kind == "unavailable":
        return httpx.Response(503)
    if kind == "malformed":
        return httpx.Response(200, json={"choices": []})
    if kind == "non-text":
        return completion(42)
    if kind == "content-list":
        return completion(["Недостаточно данных"])
    return completion(" \n\t ")


@pytest.mark.parametrize(("failure", "journal_hint"), [
    pytest.param("empty", "пустой ответ", id="empty"),
    pytest.param("timeout", "ReadTimeout", id="timeout"),
    pytest.param("unavailable", "503", id="503"),
    pytest.param("malformed", "IndexError", id="missing-choice"),
    pytest.param("non-text", "неожиданном формате", id="number-content"),
    pytest.param("content-list", "неожиданном формате", id="list-content"),
])
@pytest.mark.parametrize(("kind", "title", "source", "fact_key", "fact", "summary"), [
    pytest.param("practice", "Научно-исследовательская работа", "practice/Б2.О.01(Н).pdf", "weeks", 6, PRACTICE_SUMMARY, id="practice"),
    pytest.param("rpd", "Глубокое обучение", "rpd/Б1.В.01.pdf", "discipline", "Глубокое обучение", RPD_SUMMARY, id="rpd"),
])
def test_model_failure_preserves_extraction_and_next_run_backfills_summary(
        summary_app, failure, journal_hint, kind, title, source, fact_key, fact, summary):
    app, responses, requests = summary_app
    document_id = add_document(app, kind, title, source)
    responses.extend([lambda request: model_failure(failure, request),
                      lambda request: model_failure(failure, request),
                      lambda _: completion(summary)])

    with TestClient(app) as client:
        first = process_existing_document(app, document_id)
        initial_versions = versions(app, document_id)
        assert [row.kind for row in initial_versions] == ["primary", "final"]
        old_primary, old_final = initial_versions
        assert old_primary.data["data"][fact_key] == fact
        assert old_final.data["data"] == old_primary.data["data"]
        assert old_final.data["summary"] is None
        assert old_final.data["summary_model"] is None
        assert old_final.data["status"] in {"processed", "partial", "needs_review"}
        assert any(journal_hint in message for message in journal_messages(app, first, "ERROR"))
        route = f"/op/{PROGRAM_ID}/doc/{document_id}"
        before = client.get(route)
        assert before.status_code == 200
        parsed = BeautifulSoup(before.text, "html.parser")
        assert parsed.select_one("#h-facts")
        assert parsed.select_one("#h-sum") is None
        if kind == "practice":
            program = BeautifulSoup(client.get(f"/op/{PROGRAM_ID}").text, "html.parser")
            assert program.select_one("#praktiki tbody td:last-child").get_text(strip=True) == (
                "Краткое описание пока не опубликовано."
            )

        second = process_existing_document(app, document_id)
        assert [row.id for row in versions(app, document_id)] == [old_primary.id, old_final.id]
        assert any(journal_hint in message for message in journal_messages(app, second, "ERROR"))

        third = process_existing_document(app, document_id)
        primary, historical, current = versions(app, document_id)
        assert (primary.id, historical.id) == (old_primary.id, old_final.id)
        assert (current.kind, current.version, current.run_id) == ("final", 2, third)
        assert current.data["data"] == old_primary.data["data"]
        assert current.data["status"] == old_final.data["status"]
        assert current.data["summary_model"] == "openai: test-model"
        assert current.data["summary"] == summary
        assert client.get(f"/api/documents/{document_id}/json").json()["summary"] == summary
        assert client.get(f"/api/documents/{document_id}/json?version=1").json()["summary"] is None
        published = BeautifulSoup(client.get(route).text, "html.parser")
        assert published.select_one('[aria-labelledby="h-sum"] .app-prose').get_text(strip=True) == summary
        collection = "practices" if kind == "practice" else "disciplines"
        assert client.get(f"/api/programs/{PROGRAM_ID}").json()[collection][0]["summary"] == summary

        process_existing_document(app, document_id)
        assert [row.id for row in versions(app, document_id)] == [primary.id, historical.id, current.id]
        assert len(requests) == 3


@pytest.mark.parametrize(("kind", "data", "summary"), [
    ("oop", OOP_FACTS, OOP_SUMMARY),
    ("gia", GIA_FACTS, GIA_SUMMARY),
    ("fos_gia", {"criteria": "Содержание выпускной работы и ответы"}, FOS_SUMMARY),
    ("practice", {"practice_kind": "производственная", "weeks": 6}, PRACTICE_SUMMARY),
    ("rpd", {"discipline": "Глубокое обучение"}, RPD_SUMMARY),
])
def test_eligible_saved_summary_is_reused(summary_app, kind, data, summary):
    app, _, _ = summary_app
    document_id = add_document(app, kind, kind, f"{kind}.pdf", status="partial", data=data, summary=summary)
    app.state.robot._add_missing_summary(None, document_id, kind, "partial", PROGRAM_ID, kind)
    assert [row.data["summary"] for row in versions(app, document_id)] == [summary]


@pytest.mark.parametrize(("kind", "status", "data"), [
    ("up", "processed", {"rows": [{"name": "Алгебра"}]}),
    ("rpd", "unknown_template", {"discipline": "Алгебра"}),
    ("practice", "skipped", {"practice_kind": "учебная"}),
    ("oop", "error", OOP_FACTS),
    ("gia", "processed", None),
    ("fos_gia", "partial", {}),
])
def test_ineligible_or_missing_final_does_not_start_summary(summary_app, kind, status, data):
    app, _, _ = summary_app
    document_id = add_document(app, kind, kind, f"{kind}.pdf", status=status, data=data)
    before = [(row.id, row.data) for row in versions(app, document_id)]
    app.state.robot._add_missing_summary(None, document_id, kind, status, PROGRAM_ID, kind)
    assert [(row.id, row.data) for row in versions(app, document_id)] == before


def test_program_html_prefers_backfilled_gia_to_assessment_fallback(summary_app):
    app, responses, requests = summary_app
    robot = app.state.robot
    noise = "Оглавление........ Нераспознанный фрагмент."
    add_document(app, "oop", "ООП", "oop.pdf", status="partial", data=OOP_FACTS, summary=noise)
    gia = add_document(app, "gia", "ГИА", "gia.pdf", status="processed", data=GIA_FACTS,
                       summary="Недостаточно данных.")
    fos = add_document(app, "fos_gia", "ФОС ГИА", "fos_gia.pdf", status="processed",
                       data={"criteria": "Качество доклада."}, summary=FOS_SUMMARY)
    robot._update_page(None, PROGRAM_ID)

    with TestClient(app) as client:
        route = f"/op/{PROGRAM_ID}"
        page = BeautifulSoup(client.get(route).text, "html.parser")
        overview = page.select_one("#svedeniya .app-prose").get_text(strip=True)
        assert "Программа «Прикладной искусственный интеллект» готовит магистров." in overview
        assert "120 зачётных единиц" in overview
        assert noise not in page.get_text()
        assert page.select_one("#gia .app-prose").get_text(strip=True) == FOS_SUMMARY
        assert client.get(f"{route}/doc/{fos}", follow_redirects=False).headers["location"] == f"{route}#gia"

        responses.extend([lambda _: completion("Трудоёмкость аттестации составляет 8 месяцев."),
                          lambda _: completion(GIA_SUMMARY)])
        robot._add_missing_summary(None, gia, "gia", "processed", PROGRAM_ID, "ГИА")
        robot._update_page(None, PROGRAM_ID)

        api = client.get(f"/api/programs/{PROGRAM_ID}").json()
        assert api["gia"]["summary"] == [GIA_SUMMARY]
        page = BeautifulSoup(client.get(route).text, "html.parser")
        assert page.select_one("#gia .app-prose").get_text(strip=True) == GIA_SUMMARY
        assert [row.data["summary"] for row in versions(app, gia)] == ["Недостаточно данных.", GIA_SUMMARY]
        assert [row.data["summary"] for row in versions(app, fos)] == [FOS_SUMMARY]
        assert len(requests) == 2
