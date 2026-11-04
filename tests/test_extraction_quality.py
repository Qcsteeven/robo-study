import json
from pathlib import Path

from robot_op.extract import sections, tables
from robot_op.robot import doc_view

FIXTURES = Path(__file__).parent / "fixtures"


def case(doc_id):
    return json.loads((FIXTURES / f"extraction-{doc_id}.json").read_text())


def test_table_row_continuation_keeps_title_and_description():
    rows=[["№","Тема","Краткое содержание"],["1","Случайный лес","Алгоритм классификации на основе"],
          ["","","деревьев решений."],["2","Поиск","Задача о размещении 8 ферзей."]]
    result=tables.titled(rows)
    assert result == [{"title":"Случайный лес","summary":"Алгоритм классификации на основе деревьев решений"},
                      {"title":"Поиск","summary":"Задача о размещении 8 ферзей"}]


def test_tables_on_nonadjacent_pages_are_not_continuations():
    rows=[(1,[["№","Тема","Краткое содержание"],["1","Алгоритмы","Чистая тема."]]),
          (8,[["1","Методические указания","Чужой раздел."]])]
    assert len(tables.titled(tables.group(rows)["topics"])) == 1


def test_real_practice_uses_stage_columns_instead_of_flat_numbered_text():
    c=case(225)
    d=sections.extract(c["type"],c["text"],c["tables"])
    assert [s["title"] for s in d["stages"]] == ["Организационный","Прохождение практики","Отчетный"]
    assert "руководителя практикой от предприятия)" in " ".join(d["stages"][1]["steps"])
    assert len(d["competencies"]) == 6
    assert d["ze"] == 3 and d["control"] == ["Зачет с оценкой"]
    assert d["extraction"]["sources"]["stages"]["pages"] == [4,5]


def test_real_math_and_ml_keep_topics_not_administrative_headings():
    for doc_id,count,first in [(237,7,"Численные методы решения уравнений и систем уравнений"),
                               (239,7,"Введение в машинное обучение. Библиотеки машинного обучения")]:
        c=case(doc_id)
        d=sections.extract(c["type"],c["text"],c["tables"])
        assert len(d["topics"]) == count and d["topics"][0]["title"] == first
        if doc_id == 237:
            assert d["semesters"] == [1,2]
        assert not d["goal"]
        assert all("Методические указания" not in t["title"] for t in d["topics"])
        assert all("Дополнительная учебная литература" not in item for item in d["literature"])
        assert all(not comp["description"][:1].isdigit() for comp in d["competencies"])


def test_unstructured_numbered_section_headers_are_not_used_as_topics():
    text="""1 Планируемые результаты обучения
УК-1 Способен анализировать информацию.
2 Место дисциплины в структуре ООП
3 Объем дисциплины
3 ЗЕТ
4 Структура и содержание дисциплины
4.3 Перечень лабораторных работ
Лабораторных работ не предусмотрено
4.4 Перечень практических занятий
Практических занятий не предусмотрено
5 Перечень учебно-методического обеспечения дисциплины
5.1 Методические указания для обучающихся по освоению дисциплины
7 Основная учебная литература
1. Автор. Книга. 2025. 120 с.
8 Дополнительная учебная литература и справочная
1. Другой автор. Второй том. 2024. 8 с.
9 Ресурсы сети Интернет
1. https://example.org
"""
    d=sections.extract("rpd",text)
    assert d["topics"] == []
    assert len(d["literature"]) == 2
    assert d["literature"][1] == "Другой автор. Второй том. 2024. 8 с."
    assert any(w["field"]=="topics" for w in d["extraction"]["warnings"])


def test_legacy_view_omits_obvious_contamination_but_preserves_real_numbers():
    d={"goal":"вопросам 3","topics":[{"title":"4 Перечень практических занятий Практических занятий не предусмотрено","summary":""},
         {"title":"Алгоритмы для 3D-графики","summary":"Решение задачи о 8 ферзях на доске 8 х 8."}],
       "competencies":[{"code":"УК-2","description":"3 его жизненного цикла"}],
       "literature":["Автор. Книга. 2025. 8 с. 8 Дополнительная учебная литература и справочная"]}
    view=doc_view.build("rpd",d)
    assert "Цель" not in dict(view["facts"])
    topics=next(b["items"] for b in view["blocks"] if b["title"]=="Темы и содержание")
    assert topics == [{"title":"Алгоритмы для 3D-графики","summary":"Решение задачи о 8 ферзях на доске 8 х 8."}]
    assert view["warnings"]
    assert "3 его жизненного цикла" not in str(view)
    assert any("Книга. 2025. 8 с." in str(b) for b in view["blocks"])


def test_unrelated_table_with_different_columns_cannot_extend_topics():
    source=[(1,[["№","Тема","Краткое содержание"],["1","Анализ данных","Построение моделей"]]),
            (2,[["1","Посторонний двухколоночный список"]])]
    assert len(tables.titled(tables.group(source)["topics"])) == 1


def test_repeated_headers_keep_topics_of_both_semesters():
    source=[(1,[["№","Тема","Краткое содержание"],["1","Первый семестр","Основы"]]),
            (2,[["№","Тема","Краткое содержание"],["1","Второй семестр","Продолжение"]])]
    assert [t["title"] for t in tables.titled(tables.group(source)["topics"])] == ["Первый семестр","Второй семестр"]


def test_structural_warning_marks_document_for_review(robot):
    import io
    from reportlab.pdfgen import canvas
    from demo_site.render import _register_fonts
    from robot_op.models import Document
    _register_fonts()
    output=io.BytesIO()
    pdf=canvas.Canvas(output)
    pdf.setFont("DejaVu",10)
    lines=["Рабочая программа дисциплины", "1 Перечень планируемых результатов обучения", "УК-1 Способен анализировать информацию.",
           "2 Место дисциплины в структуре ООП", "Дисциплина базируется на математике.","3 Объем дисциплины","Объем дисциплины: 3 ЗЕТ",
           "4 Структура и содержание дисциплины", "4.4 Перечень практических занятий", "Практических занятий не предусмотрено",
           "6 Оценочные средства", "Экзамен.","7 Основная учебная литература","1. Автор. Книга. 2025. 120 с."]
    for i,line in enumerate(lines):
        pdf.drawString(40,800-i*18,line)
    pdf.save()
    status,_,_,data,_=robot._extract(1,Document(doc_type="rpd"),output.getvalue(),{"id":"test"},"РПД")
    assert status=="needs_review" and data["topics"]==[]


def test_topic_mention_of_literature_cannot_start_bibliography_section():
    text="""4 Структура и содержание дисциплины
9 Сетевая стилометрия литературных произведений
Текст темы.
4.4 Перечень практических занятий
Практических занятий не предусмотрено.
7 Основная учебная литература
1. Автор. Настоящая книга. 2025. 100 с.
8 Дополнительная учебная и справочная литература
1. Другой автор. Вторая книга. 2024. 8 с.
9 Ресурсы сети Интернет
1. https://example.org
"""
    d=sections.extract("rpd",text)
    assert d["literature"]==["Автор. Настоящая книга. 2025. 100 с.","Другой автор. Вторая книга. 2024. 8 с."]


def test_bibliography_headers_can_be_unnumbered_and_have_reference_modifier():
    text="1. Автор. Книга. 2025. 8 с.\nДополнительная учебная литература\n1. Второй автор. Книга. 2024. 150 с."
    assert sections.literature_items(text)==["Автор. Книга. 2025. 8 с.","Второй автор. Книга. 2024. 150 с."]
    view=doc_view.build("rpd",{"literature":["Автор. Книга. 2025. 8 с. 8 Дополнительная учебная и справочная литература"]})
    assert next(b["items"] for b in view["blocks"] if b["title"]=="Литература")==["Автор. Книга. 2025. 8 с."]


def test_numeric_grid_and_stray_outline_page_number_are_flagged_not_rewritten():
    from robot_op.extract.quality import validate
    data={"topics":[{"title":"Технологии топливо-энергосбережения в 1 3 1 6 1, 3, 2 9 Собеседование строительстве","summary":"Текст"},
                    {"title":"Раздел 4 Политическое устройство России 7 4.1.Конституционные принципы","summary":"Текст"},
                    {"title":"Основы 3D-графики и сети 5G","summary":"Задача о 8 ферзях на доске 8 х 8"}]}
    clean=validate("rpd",data)
    assert clean["topics"]==[data["topics"][2]]
    assert clean["extraction"]["warnings"]
    assert len(data["topics"])==3


def test_flattened_competency_table_is_not_treated_as_reliable_description():
    text="""1 Перечень планируемых результатов обучения
Код, наименование компетенции Код индикатора компетенции
УК-2 Способен управлять проектом на всех этапах
УК-2.3
его жизненного цикла
2 Место дисциплины в структуре ООП
Текст.
"""
    d=sections.extract("rpd",text)
    assert d["competencies"]==[{"code":"УК-2","description":""}]
    assert any(w["field"]=="competencies" for w in d["extraction"]["warnings"])


def test_practice_table_pointer_and_page_footer_are_not_content():
    text="""4 Содержание практики
2
Выполнение практических заданий, экскурсии в ИТ-компании
Содержание этапов приведено в таблице ниже:
№ п/п Этап Содержание работ
5 Формы отчетности
1. Отчет.
"""
    d=sections.extract("practice",text)
    assert d["content"]=="Выполнение практических заданий, экскурсии в ИТ-компании"
    only_pointer=sections.extract("practice","4 Содержание практики\nСодержание этапов приведено в таблице ниже:\n№ п/п Этап Содержание работ")
    assert only_pointer["content"]==""


def test_semester_header_can_split_inside_letters_in_a_narrow_column():
    rows=[["Вид учебной работы","Трудоемкость в академических часах","", ""],
          ["","Всего","С е м ес тр № 2","Се ме ст р № 4"],
          ["Общая трудоемкость дисциплины","72","36","36"]]
    assert tables.volume(rows)["semesters"]==[2,4]
