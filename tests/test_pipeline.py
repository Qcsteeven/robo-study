import json

import httpx
from sqlalchemy import func, select

from conftest import demo_settings, needs_tesseract
from demo_site.site import mock_transport
from robot_op.models import Database, Document, JsonVersion, LogEntry, Program, Run, RunItem
from robot_op.robot.pipeline import Robot

pytestmark = needs_tesseract
KEY = "09.03.01-isoiu-2023-och"


def statuses(robot, run_id):
    with robot.db.Session() as ses:
        return {(i.doc_type, i.title): i.status for i in ses.scalars(select(RunItem).where(RunItem.run_id == run_id))}


def test_full_run_and_rerun(robot):
    run_id = robot.create_run("bachelor", 2023, [KEY])
    robot.execute(run_id)
    st = statuses(robot, run_id)
    with robot.db.Session() as ses:
        run = ses.get(Run, run_id)
        assert run.status == "finished"
        counts = Robot.counts(ses, run_id)
        assert counts["total"] == 22 and counts["done"] == 22
        # альтернативные сценарии ТЗ
        assert st[("up", "Учебный план")] == "needs_review"                       # расхождение сумм часов
        assert st[("kug", "Календарный учебный график")] == "processed"
        assert st[("fos_gia", "ФОС ГИА")] == "partial"                            # нет раздела критериев
        assert st[("rpd", "Обработка естественного языка")] == "unknown_template"
        assert st[("rpd", "Основы проектной деятельности")] == "error"            # битая ссылка
        assert st[("practice", "Учебная практика: технологическая (проектно-технологическая) практика")] == "skipped"
        assert ses.scalar(select(func.count()).select_from(LogEntry).where(
            LogEntry.run_id == run_id, LogEntry.level == "WARNING", LogEntry.message.like("%нет ссылки%"))) == 0
        # первичный и итоговый JSON по каждому обработанному документу; сведения об ОП — со страницы сайта
        doc = ses.scalar(select(Document).where(Document.program_id == KEY, Document.doc_type == "up"))
        final = ses.scalar(select(JsonVersion).where(JsonVersion.document_id == doc.id, JsonVersion.kind == "final"))
        assert final.data["program"]["code"] == "09.03.01" and final.data["program"]["year"] == 2023
        assert final.data["document"]["method"] == "ocr"
        page = ses.get(Program, KEY).page
        assert page["curriculum"]["groups"] and page["schedule"]["courses"]
        assert len(page["disciplines"]) == 13 and len(page["practices"]) == 4
        versions_before = ses.scalar(select(func.count()).select_from(JsonVersion))
    # повторный запуск: документы не изменились — не скачиваются заново (304) и не переобрабатываются
    run2 = robot.create_run("bachelor", 2023, [KEY])
    robot.execute(run2)
    with robot.db.Session() as ses:
        assert ses.scalar(select(func.count()).select_from(JsonVersion)) == versions_before
        assert ses.scalar(select(func.count()).select_from(LogEntry).where(
            LogEntry.run_id == run2, LogEntry.message.like("%не изменился%"))) >= 15
    assert statuses(robot, run2) == {k: v for k, v in st.items()}


def test_new_json_version_when_document_changes(tmp_path):
    settings = demo_settings(tmp_path)
    db = Database(settings.app.database_url)
    db.create_all()
    base = mock_transport()
    changed = {"on": False}

    def handler(request):
        if changed["on"] and request.url.path.endswith("/oop.pdf"):
            from demo_site import render
            from demo_site.data import ISOIU
            import dataclasses
            p = dataclasses.replace(ISOIU, oop_text=dict(ISOIU.oop_text, partners="новые партнёры"))
            return httpx.Response(200, content=render.oop_pdf(p), headers={"content-type": "application/pdf"})
        return base.handle_request(request)
    robot = Robot(settings, db, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    robot.execute(robot.create_run("bachelor", 2023, [KEY]))
    changed["on"] = True
    robot.execute(robot.create_run("bachelor", 2023, [KEY]))
    with db.Session() as ses:
        oop = ses.scalar(select(Document).where(Document.program_id == KEY, Document.doc_type == "oop"))
        vs = ses.scalars(select(JsonVersion).where(JsonVersion.document_id == oop.id, JsonVersion.kind == "primary")
                         .order_by(JsonVersion.version)).all()
        assert [v.version for v in vs] == [1, 2]
        assert "новые партнёры" in json.dumps(vs[-1].data, ensure_ascii=False)


def test_interrupted_run_resumes_without_redownload(robot):
    run_id = robot.create_run("bachelor", 2023, ["09.03.03-pie-2023-och"])
    calls = {"n": 0}
    original = robot._document

    def stop_after_three(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 4:
            robot.stop()
        return original(*a, **kw)
    robot._document = stop_after_three
    robot.execute(run_id)
    with robot.db.Session() as ses:
        assert ses.get(Run, run_id).status == "interrupted"
        done_first = ses.scalar(select(func.count()).select_from(RunItem).where(RunItem.run_id == run_id, RunItem.done.is_(True)))
    assert 0 < done_first < 12
    robot._document = original
    robot.execute(run_id)
    with robot.db.Session() as ses:
        assert ses.get(Run, run_id).status == "finished"
        assert Robot.counts(ses, run_id)["done"] == 12


def test_llm_summaries_and_llm_failure(tmp_path):
    prompts = []

    def ollama(request):
        body = json.loads(request.content)
        prompts.append(body)
        return httpx.Response(200, json={"message": {"content": "Краткое описание документа."}})
    settings = demo_settings(tmp_path, ROBOT_LLM_PROVIDER="ollama", ROBOT_LLM_BASE_URL="http://llm.local:11434")
    db = Database(settings.app.database_url)
    db.create_all()
    robot = Robot(settings, db, transport=mock_transport(), llm_transport=httpx.MockTransport(ollama), sleep=lambda s: None)
    robot.execute(robot.create_run("master", 2024, ["*"]))
    with db.Session() as ses:
        page = ses.scalars(select(Program)).first().page
        assert page["op"]["summary"] == "Краткое описание документа."
        assert all(d["summary"] for d in page["disciplines"])
    # в LLM уходят только сведения первичного JSON
    assert prompts and all("Сведения документа" in p["messages"][1]["content"] for p in prompts)

    def broken(request):
        return httpx.Response(500)
    (tmp_path / "b").mkdir()
    settings2 = demo_settings(tmp_path / "b", ROBOT_LLM_PROVIDER="ollama", ROBOT_LLM_BASE_URL="http://llm.local:11434")
    db2 = Database(f"sqlite:///{tmp_path}/b/robot.db")
    db2.create_all()
    robot2 = Robot(settings2, db2, transport=mock_transport(), llm_transport=httpx.MockTransport(broken), sleep=lambda s: None)
    rid = robot2.create_run("master", 2024, ["*"])
    robot2.execute(rid)
    with db2.Session() as ses:
        assert ses.get(Run, rid).status == "finished"
        assert ses.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.message.like("%LLM недоступен%"))) > 0
        final = ses.scalars(select(JsonVersion).where(JsonVersion.kind == "final")).first()
        assert final.data["summary"] is None
