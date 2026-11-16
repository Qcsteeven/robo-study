import io
import json

import httpx
import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from robot_op.config import LlmSettings
from robot_op.extract import sections
from robot_op.models import Document, JsonVersion, Program
from robot_op.robot.llm import LlmClient, LlmError, compact
from robot_op.robot.page import build

GIA = """Программа государственной итоговой аттестации
\f
Содержание
Общие положения ............... 4
1.2.4. Процедура защиты выпускной квалификационной работы ............... 8
1.3 Критерии оценки результатов защиты выпускной квалификационной
работы ............... 10
\f
Общие положения
Цель государственной итоговой аттестации (ГИА) – установление уровня подготовки.
Трудоемкость ГИА составляет 216 часов. Форма проведения государственной
итоговой аттестации – защита выпускной квалификационной работы (ВКР). Вид ВКР –
дипломная работа.
1 Подготовка и защита выпускной квалификационной работы
1.1 Перечень компетенций
УК ОС-1. Способность выполнять критический анализ.
1.2 Требования к выпускной квалификационной работе
1.2.3 Требования к объему, структуре и оформлению выпускной
квалификационной работы
Минимальный объем пояснительной записки составляет 60 страниц.
1.2.4. Процедура защиты выпускной квалификационной работы
Выпускник делает доклад и отвечает на вопросы членов комиссии.
1.3 Критерии оценки результатов защиты выпускной квалификационной работы
Учитываются самостоятельность работы и качество доклада.
2 Порядок подачи и рассмотрения апелляций
Апелляцию подают в комиссию.
"""


def test_gia_reads_workload_in_general_provisions_and_actual_numbered_sections():
    data = sections.extract("gia", GIA)
    assert data["forms"] == ["Защита выпускной квалификационной работы (ВКР)"]
    assert data["hours"] == 216 and data["ze"] is None
    assert data["vkr_kind"] == "дипломная работа"
    assert "60 страниц" in data["vkr_requirements"]
    assert data["procedure"].startswith("Выпускник делает доклад")
    assert data["criteria"].startswith("Учитываются самостоятельность")
    assert data["template"]["missing_required"] == []


def test_fos_uses_assessment_scale_and_preserves_university_standard_codes():
    text = """Фонд оценочных средств государственной итоговой аттестации
1 Перечень компетенций, которыми должны овладеть выпускники
УК ОС-1. Способность выполнять критический анализ.
ОПК ОС - 2. Способность проводить измерения.
ПКС-1. Способность анализировать требования.
2 Индикаторы (показатели) и критерии оценивания сформированности компетенций
Критерии оценивания Способ оценивания
Содержание ВКР, доклад и ответы на вопросы.
3 Шкалы оценивания
3.1 Шкала оценивания результатов защиты ВКР
Оцениваются качество работы и ответы комиссии.
4 Типовые задания
— Проанализировать исходные требования.
"""
    data = sections.extract("fos_gia", text)
    assert data["competencies"] == ["УК ОС-1", "ОПК ОС-2", "ПКС-1"]
    assert "Содержание ВКР" in data["indicators"]
    assert "Оцениваются качество работы" in data["criteria"]
    assert data["template"]["missing_required"] == []


def test_oop_reads_attestation_form_with_ocr_artifacts():
    data = sections.extract("oop", """Основная образовательная программа
1 Общая характеристика образовательной программы
Форма государственной итоговой — аттестации: защита ___ выпускной
квалификационной работы
Подразделение, ответственное за программу: институт
""")
    assert data["gia_forms"] == ["Защита выпускной квалификационной работы"]


def test_oop_reads_scan_pages_when_approval_page_has_abundant_text(robot, monkeypatch):
    pdf = io.BytesIO()
    doc = canvas.Canvas(pdf)
    scan = ImageReader(Image.new("RGB", (400, 600), "white"))
    doc.drawImage(scan, 0, 0, width=400, height=600)
    doc.showPage()
    doc.drawString(10, 800, "Approval and signature page. " * 12)
    doc.showPage()
    doc.drawImage(scan, 0, 0, width=400, height=600)
    doc.save()
    ocr_text = iter(["Основная образовательная программа",
        "1 Общая характеристика программы\nКвалификация: Магистр\n"
        "Нормативный срок освоения ООП: 2 года\nТрудоемкость ООП: 120 зачётных единиц\n"
        "2 Характеристика профессиональной деятельности\n— 06 Информационные технологии\n"
        "3 Планируемые результаты освоения\nРазработка информационных систем."])
    monkeypatch.setattr("robot_op.robot.pipeline.ocr_page_text", lambda *_: next(ocr_text))
    status, _, method, data, pages = robot._extract(None, Document(doc_type="oop"), pdf.getvalue(), {"id": "test"}, "ООП")
    assert (status, method, pages) == ("processed", "ocr", 3)
    assert (data["qualification"].lower(), data["duration"], data["ze_total"]) == ("магистр", "2 года", 120)


@pytest.mark.parametrize("doc_type, data, expected", [
    ("gia", {"forms": ["Защита ВКР"], "hours": 216, "procedure": "Доклад."}, "hours"),
    ("fos_gia", {"competencies": ["УК ОС-1"], "criteria": "Качество доклада."}, "criteria"),
])
def test_attestation_prompt_uses_structured_fields_within_budget(doc_type, data, expected):
    payload = compact(dict(data, sections={"general": {"text": "Оглавление" * 5000}},
                           resources=[{"url": "https://www.istu.edu/"}], characteristic="Сырой текст" * 100),
                      100, doc_type)
    assert len(payload) <= 100
    obj = json.loads(payload)
    assert obj[expected] == data[expected]
    assert not {"sections", "resources", "characteristic"} & obj.keys()


@pytest.mark.parametrize("doc_type", ["gia", "fos_gia"])
def test_attestation_rejects_model_refusal(doc_type):
    llm = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "Недостаточно данных."}}]})))
    with pytest.raises(LlmError):
        llm.summarize(doc_type, {"forms": ["Защита ВКР"]} if doc_type == "gia" else {"criteria": "Качество доклада."})


def test_program_page_uses_fos_summary_when_gia_model_refuses(robot):
    with robot.db.Session() as ses:
        program = Program(id="ai", level="master", level_title="Магистратура", year=2026)
        ses.add(program)
        ses.add_all([
            Document(id=1, program_id="ai", doc_type="gia", title="ГИА", url="https://www.istu.edu/gia.pdf"),
            Document(id=2, program_id="ai", doc_type="fos_gia", title="ФОС ГИА", url="https://www.istu.edu/fos.pdf"),
        ])
        ses.flush()
        assessment = "Комиссия оценивает содержание работы, качество доклада и ответы на вопросы."
        ses.add_all([
            JsonVersion(document_id=1, kind="final", version=1, data={
                "data": {"forms": ["Защита выпускной квалификационной работы (ВКР)"], "hours": 324},
                "summary": "Недостаточно данных.",
            }),
            JsonVersion(document_id=2, kind="final", version=1, data={
                "data": {"criteria": "Качество доклада."},
                "summary": assessment,
            }),
        ])
        ses.flush()
        gia = build(ses, program)["gia"]
        assert gia["summary"] == [assessment]
        assert {"label": "Объём", "value": "324 ч"} in gia["items"]


def test_gia_rejects_invented_conversion_of_workload():
    reply = "Защита ВКР занимает 216 часов, что составляет около 8 месяцев."
    llm = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
    with pytest.raises(LlmError, match="трудоёмкост"):
        llm.summarize("gia", {"forms": ["Защита ВКР"], "hours": 216})


def test_gia_limits_total_sentences_across_paragraphs():
    reply = ("Аттестация проходит в форме защиты ВКР. Трудоёмкость ГИА составляет 216 часов. "
             "Работа включает пояснительную записку.\n\n"
             "Выпускник выступает перед комиссией. После доклада он отвечает на вопросы.")
    llm = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})))
    result = llm.summarize("gia", {"forms": ["Защита ВКР"], "hours": 216})
    assert "\n\nВыпускник выступает перед комиссией." in result
    assert "После доклада" not in result


def test_gia_prompt_omits_example_abstract_and_keeps_requirements():
    data = {"hours": 324, "vkr_requirements": (
        "Работа включает пояснительную записку.\nПример аннотации:\nРабота написана на 62 страницах.")}
    payload = json.loads(compact(data, 12000, "gia"))
    assert payload["vkr_requirements"] == "Работа включает пояснительную записку."


@pytest.mark.parametrize("doc_type, invalid, corrected, facts", [
    ("oop", "Недостаточно данных.",
     "Программа готовит магистров. Обучение длится два года. Выпускники проектируют информационные системы.",
     {"qualification": "Магистр", "duration": "2 года", "task_types": ["проектный"]}),
    ("gia", "ГИА включает защиту ВКР. Работа написана на 62 страницах и содержит 3 таблицы.",
     "ГИА проходит в форме защиты ВКР. Трудоёмкость аттестации составляет 324 часа. Выпускник выступает перед комиссией.",
     {"forms": ["Защита ВКР"], "hours": 324, "procedure": "Защита проходит перед комиссией."}),
])
def test_invalid_summary_is_retried_once_with_calibrated_prompt(doc_type, invalid, corrected, facts):
    replies = iter([invalid, corrected])
    requests = []

    def complete(request):
        requests.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    client = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(complete))
    assert client.summarize(doc_type, facts) == corrected
    assert len(requests) == 2 and requests[0] != requests[1]


def test_summary_does_not_retry_a_second_invalid_response():
    requests = []

    def complete(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Трудоёмкость ГИА составляет 8 месяцев."}}]})

    client = LlmClient(LlmSettings(provider="openai"), transport=httpx.MockTransport(complete))
    with pytest.raises(LlmError, match="трудоёмкост"):
        client.summarize("gia", {"forms": ["Защита ВКР"], "hours": 324})
    assert len(requests) == 2
