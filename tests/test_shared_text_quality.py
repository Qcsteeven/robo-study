import json
from pathlib import Path

import pytest

from robot_op.extract import sections, tables
from robot_op.extract.lists import stage_lists
from robot_op.robot import doc_view, program_summary

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(doc_id):
    return json.loads((FIXTURES / f"extraction-{doc_id}.json").read_text())


def extract(doc_id):
    c = fixture(doc_id)
    return sections.extract(c["type"], c["text"], c["tables"])


def test_academic_writing_results_have_content_without_bare_duplicate_labels():
    d = extract(264)
    assert len(d["results"]) == 3
    assert [x.split()[0] for x in d["results"]] == ["Знать", "Уметь", "Владеть"]
    assert "реферат-обзор" in d["results"][1]
    assert "созданных другими авторами" in d["results"][2]


def test_old_saved_results_receive_the_same_cleanup():
    d = {"results": ["Знать", "Знать Знать основы 5G", "Уметь:", "Уметь читать", "Владеть", "Владеть навыками"]}
    block = next(b for b in doc_view.build("rpd", d)["blocks"] if b["title"] == "Результаты обучения")
    assert block["items"] == ["Знать основы 5G", "Уметь читать", "Владеть навыками"]
    assert d["results"][0] == "Знать"


def test_pedagogical_practice_symbol_bullets_have_named_groups():
    d = extract(353)
    stage = d["stages"][1]
    assert stage["summary"].endswith("Формы и виды работ:")
    assert [g["title"] for g in stage["groups"]] == ["Учебно-методическая работа", "Организация педагогического процесса", "Организационно-воспитательная работа"]
    assert [len(g["steps"]) for g in stage["groups"]] == [8, 8, 2]
    assert all(not g["ordered"] for g in stage["groups"])
    assert "изучение рабочих программ 1-2" in stage["groups"][0]["steps"][3]
    assert "\uf02d" not in json.dumps(d["stages"], ensure_ascii=False)
    shown = next(b for b in doc_view.build("practice", d)["blocks"] if b["title"] == "Этапы практики")
    assert shown["items"][1]["groups"] == stage["groups"]


def test_symbol_normalization_is_targeted_and_preserves_semantic_numbers():
    items = stage_lists([{"title": "Работа", "summary": "Задания: \uf0b7 Python 3.11 и 1.5 \uf02d изучить 1-2 программы и 5G"}])
    assert items[0]["summary"] == "Задания:"
    assert items[0]["steps"] == ["Python 3.11 и 1.5", "изучить 1-2 программы и 5G"]
    unknown = "Формула \ue001 и знак минус - сохраняются"
    assert stage_lists([{"title": "Формула", "summary": unknown}])[0]["summary"] == unknown


def test_spo_oop_uses_title_qualification_and_full_multiline_duration():
    d = extract(306)
    assert d["qualification"].lower() == "специалист по компьютерным системам"
    assert d["duration"] == "3 года 10 месяцев"
    assert program_summary.facts(d)["duration"] == "3 года 10 месяцев"
    assert "3 года 10 месяцев" in program_summary.fallback(d, {"profile": "Компьютерные системы и комплексы"})


def test_oop_table_header_never_becomes_a_qualification_or_summary_fact():
    text = "Квалификации:\nНаименование видов деятельности Наименование профессиональных модулей"
    assert not sections.extract("oop", text)["qualification"]
    dirty = {"qualification": "Наименование видов деятельности Наименование профессиональных"}
    assert "qualification" not in program_summary.facts(dirty)
    assert "Квалификация" not in dict(doc_view.build("oop", dirty)["facts"])


@pytest.mark.parametrize("doc_id,kind,hours,semester,weeks,stages", [
    (309,"Производственная практика",180,4,None,3),
    (310,"Производственная практика",252,8,8,5),
    (311,"Производственная практика",252,8,3,3),
    (312,"Учебная практика",72,3,None,4),
    (313,"Учебная практика",72,7,2,5),
    (314,"Учебная практика",108,8,3,3),
])
def test_spo_practice_passport_and_table_content(doc_id, kind, hours, semester, weeks, stages):
    d = extract(doc_id)
    assert d["practice_kind"] == kind
    assert d["hours"] == {"total": hours}
    assert d["semesters"] == [semester]
    assert d["weeks"] == weeks
    assert d["ze"] is None  # hours are not credits
    assert len(d["stages"]) == stages
    assert all("Тема 1.1" not in s["title"] for s in d["stages"])
    assert all(s.get("steps") for s in d["stages"])
    assert not d["method"] and not d["form"]  # source has no explicit fields
    assert f"{hours} ч" in dict(doc_view.build("practice", d)["facts"])["Объём"]


def test_spo_source_hours_disagreement_is_retained_and_reported():
    d = extract(312)
    assert d["hours"]["total"] == 72
    assert sum(s.get("hours", 0) for s in d["stages"]) == 82
    assert any(w["field"] == "hours" and "82" in w["reason"] and "72" in w["reason"] for w in d["extraction"]["warnings"])


def test_native_pdf_table_open_bottom_retains_final_activity():
    from robot_op.extract import pdf
    rows = pdf.page_tables((FIXTURES / "education_plans" / "practice_310_page6.pdf").read_bytes())
    cells = [cell for _, table in rows for row in table for cell in row]
    assert any("Создание алгоритма и программы для заданной" in c and "микроконтроллера" in c for c in cells)


def test_numbered_substages_in_practice_title_become_a_list():
    d = extract(353)
    stage = d["stages"][0]
    assert stage["title"] == "Организационно-подготовительный этап"
    assert stage["subtitles"] == ["Организационное собрание или собеседование, подготовка индивидуального плана", "Теоретическая подготовка к решению задач практики"]
    assert d["stages"][1]["title"] == "Основной этап практики"
