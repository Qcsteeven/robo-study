import json

import httpx
import pytest

from robot_op.config import LlmSettings
from robot_op.extract import sections
from robot_op.models import Database, Document, JsonVersion, Program
from robot_op.robot.llm import LlmClient, LlmError, compact
from robot_op.robot.page import build


NOISE = "Характеристика профессиональной деятельности....... ананнинининннни"
SCANNED = """Основная образовательная программа
\f
СОДЕРЖАНИЕ
Общая характеристика образовательной программы
Характеристика профессиональной деятельности выпускника ООП.......
Планируемые результаты освоения образовательной программы..........
4 | Сведения о профессорско-преподавательском составе
реализации образовательной программы....... ананнинининннни
3
\f
1 Общая характеристика образовательной программы
1.1 Основная образовательная программа представляет собой систему документов,
разработанную в соответствии с федеральным государственным стандартом и приказом № 918.
Квалификация: Магистр
Нормативный срок освоения ООП: 2 года
Трудоемкость ООП: 120 зачетных единиц.
Форма государственной итоговой аттестации: защита выпускной квалификационной работы
2 Характеристика профессиональной деятельности выпускника ООП
241 Область профессиональной деятельности и (или) сферы профессиональной
деятельности.
— 06 Связь, информационные и коммуникационные технологии (в сфере проектирования,
разработки и модернизации средств вычислительной техники и информационных систем)
2.2 — Типы задач профессиональной деятельности выпускников:
научно-исследовательские;
производственно-технологические;
проектные.
2.3 Образовательная программа разработана с учетом требований профессиональных стандартов
Таблица стандартов, а не описание программы.
3 Планируемые результаты освоения образовательной программы
УК-1 Способен анализировать проблемные ситуации.
"""


def facts():
    return {"qualification": "магистр", "duration": "2 года", "ze_total": 120,
            "areas": ["06 Связь, информационные и коммуникационные технологии"],
            "task_types": ["научно-исследовательские", "производственно-технологические", "проектные"],
            "characteristic": NOISE, "sections": {"general": {"text": NOISE}}}


def test_scanned_oop_contents_is_not_the_general_section():
    data = sections.extract("oop", SCANNED)
    assert "ананни" not in data["sections"]["general"]["text"]
    assert "1.1 Основная образовательная программа" in data["sections"]["general"]["text"]
    assert data["qualification"].lower() == "магистр"
    assert data["duration"] == "2 года" and data["ze_total"] == 120
    assert data["areas"] == ["06 Связь, информационные и коммуникационные технологии (в сфере проектирования, разработки и модернизации средств вычислительной техники и информационных систем)"]
    assert data["task_types"] == ["научно-исследовательские", "производственно-технологические", "проектные"]


def test_oop_llm_receives_only_structured_facts():
    primary = dict(facts(), program={"profile": "Искусственный интеллект"})
    payload = json.loads(compact(primary, 12000, "oop"))
    assert "characteristic" not in payload and "sections" not in payload
    assert "ананни" not in json.dumps(payload, ensure_ascii=False)
    assert payload["areas"] == facts()["areas"] and payload["program"]["profile"] == "Искусственный интеллект"
    empty = json.loads(compact({"characteristic": NOISE, "sections": {"general": {"text": NOISE}}}, 12000, "oop"))
    assert "ананни" not in json.dumps(empty, ensure_ascii=False)


def test_oop_llm_rejects_contents_and_legal_boilerplate():
    for reply in (NOISE, "Программа представляет собой систему документов в соответствии с приказом № 918."):
        client = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
        with pytest.raises(LlmError):
            client.summarize("oop", facts())


def test_program_page_uses_facts_instead_of_noisy_excerpt(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/robot.db"); db.create_all()
    with db.Session() as ses:
        prog = Program(id="ai", level="master", level_title="Магистратура", year=2026,
                       code="09.04.01", profile="Искусственный интеллект", form="Очная")
        ses.add(prog)
        ses.add(Document(id=1, program_id="ai", doc_type="oop", title="ООП", url="https://www.istu.edu/oop.pdf"))
        ses.flush()
        ses.add(JsonVersion(document_id=1, kind="final", version=1, data={"data": facts(), "summary": NOISE}))
        ses.flush()
        op = build(ses, prog)["op"]
        assert not op["summary"]
        description = op["characteristic"]
        assert "ананни" not in description and "......." not in description
        assert "Искусственный интеллект" in description and "2 года" in description and "120" in description
        assert "проект" in description and len(description) <= 800


def test_valid_llm_summary_is_preserved(tmp_path):
    reply = ("Программа готовит магистров для исследовательской и проектной работы в области информационных технологий. "
             "Обучение длится два года. Выпускники выполняют научные и прикладные проекты.")
    client = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
    assert client.summarize("oop", facts()) == reply


def test_ocr_subsection_number_is_not_a_professional_area():
    text = SCANNED.replace("241 Область", "24 Области").replace("120 зачетных", "120 __ зачетных")
    data = sections.extract("oop", text)
    assert len(data["areas"]) == 1 and data["areas"][0].startswith("06 Связь")
    assert data["ze_total"] == 120


def test_long_area_keeps_its_short_name_in_fallback():
    from robot_op.robot.program_summary import fallback
    data = dict(facts(), areas=["16 Строительство и жилищно-коммунальное хозяйство (" + "объекты строительства, " * 22 + ")"])
    description = fallback(data, {"profile": "Водоснабжение и водоотведение"})
    assert "строительство и жилищно-коммунальное хозяйство" in description
    assert len(description) <= 800


def test_oop_compact_respects_context_limit_without_breaking_json():
    primary = dict(facts(), program={"profile": "Искусственный интеллект"})
    payload = compact(primary, 200, "oop")
    assert len(payload) <= 200
    assert isinstance(json.loads(payload), dict)


def test_overlong_oop_llm_answer_is_rejected():
    reply = "Программа готовит " + "специалистов по разработке информационных систем, " * 25 + "и исследователей."
    client = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
    with pytest.raises(LlmError):
        client.summarize("oop", facts())


def test_oop_llm_rejects_empty_meaning_and_abbreviated_legal_text():
    for reply in ("Недостаточно данных.", "Программа соответствует ФГОС. Основание: приказ Минобрнауки от 12.03.2021. Общая характеристика приведена в документах."):
        client = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
        with pytest.raises(LlmError):
            client.summarize("oop", facts())


def test_invalid_saved_oop_summary_can_be_regenerated_without_reparsing(tmp_path):
    from conftest import demo_settings
    from robot_op.robot.pipeline import Robot
    from sqlalchemy import select
    settings = demo_settings(tmp_path, ROBOT_LLM_PROVIDER="openai")
    db = Database(settings.app.database_url); db.create_all()
    reply = "Программа готовит магистров для проектной работы. Обучение длится два года. Выпускники выполняют исследования."
    with db.Session() as ses:
        ses.add(Program(id="ai", level="master", level_title="Магистратура", year=2026))
        ses.add(Document(id=1, program_id="ai", doc_type="oop", title="ООП", url="https://www.istu.edu/oop.pdf"))
        ses.flush()
        ses.add_all([JsonVersion(document_id=1, kind="primary", version=1, data={"data": facts()}),
                     JsonVersion(document_id=1, kind="final", version=1, data={"data": facts(), "summary": "Недостаточно данных."})])
        ses.commit()
    robot = Robot(settings, db, llm_transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
    robot._add_missing_summary(None, 1, "oop", "processed", "ai", "ООП")
    with db.Session() as ses:
        versions = ses.scalars(select(JsonVersion).where(JsonVersion.kind == "final").order_by(JsonVersion.version)).all()
        assert [v.version for v in versions] == [1, 2]
        assert versions[-1].data["summary"] == reply
        primary = ses.scalar(select(JsonVersion).where(JsonVersion.kind == "primary"))
        assert primary.version == 1 and primary.data["data"] == facts()
